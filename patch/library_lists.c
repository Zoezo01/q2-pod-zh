/* The Library's own list pages: Shuffle, Most Played, the card's songs (Tidal Downloads and a card
 * app's local rows), Era and Long Songs. */
#include "navigation.h"

/* Tidal Downloads' (or a card app's) songs page and its songs, its timer, and whether it shows
 * Clear All Downloads' confirmation; the library generation Most Played's kept tracks are of
 * (~library_gen once stale); the Library Shuffle menu's page, kept across picks. */
static struct {
    void *td_page, *td_list;
    unsigned td_timer;
    int td_asking;
    unsigned mp_gen;
    void *sp_page;
} ll __attribute__((section(".scratch")));

extern int coverflow_goto_album(void *r);

/* Collection Shuffle is Shuffle Songs over the collection. */
int shuffle_play(void *all) {
    int size = (int)deque_size(all);
    if (size) {
        int at = (int)rand_below((unsigned)size);
        if (playback_keeps(all, at)) { /* not a pick of it: another song, or alone, Now Playing */
            if (size == 1) {
                playback_np();
                return size;
            }
            at = (at + 1 + (int)rand_below((unsigned)size - 1)) % size;
        }
        int mode = playback_unshuffle();
        playback_clear(); /* stock Shuffle keeps Shuffle Albums/Folders otherwise */
        config_playmode(2, 1);
        play_folder(all, at, 0xf001);
        playback_shuffle(mode); /* an order Up Next can list; stock's shuffle stays the fallback */
    }
    return size;
}

static int shuffle_songs(void *ctx, void *event) {
    int n;
    void *all = staged(songs_unsorted, (void *)1, &n);
    if (!shuffle_play(all)) toast("Update Local Music first");
    deque_destroy(all);
    return 0;
}

static int shuffle_groups(void *ctx, void *event) {
    int n;
    void *all = staged(songs_unsorted, (void *)1, &n);
    if (!playback_groups(all, (int)(long)ctx))
        toast(deque_size(all) ? "Too many songs to shuffle" : "Update Local Music first");
    deque_destroy(all);
    return 0;
}

/* The Library's Shuffle row opens these three actions on one page. A pick starts playback and
 * leaves the page open, as Most Played's rows do. Album and folder shuffles queue and group the
 * whole library, hence the note. */
static int sp_pick(void *ctx, void *event) {
    if ((long)ctx)
        shuffle_groups((void *)((long)ctx - 1), 0);
    else
        shuffle_songs(0, 0);
    return 0;
}

static int sp_closed(void *ctx, void *event) {
    ll.sp_page = 0;
    return 0;
}

int shuffle_menu(void *ctx, void *event) {
    if (ll.sp_page || !(ll.sp_page = page_open("shuffle_page", sp_closed, page_keyup))) return 0;
    void *view = page_list(ll.sp_page, ll.sp_page, 0, "Shuffle", 3, 48);
    page_row_detail(view, 0, "Shuffle Songs", 0, sp_pick);
    page_row_detail(view, 1, "Shuffle Albums", 0, sp_pick);
    page_row_detail(view, 2, "Shuffle Folders", 0, sp_pick);
    /* With the title in the bar: centred at the page's foot, its box clear of the glass. */
    void *note = theme()->page_title_h ? text(ll.sp_page, 20, 200, 335, 24)
                                       : text(ll.sp_page, 46, 262, 283, 24);
    widget_set_prop_int(note, "style:normal:text_color", (int)0xffaaaaaau);
    widget_set_text_utf8(note, "Albums and folders take a moment to shuffle");
    theme_font(note, ROLE_INFO, FONT_ROW);
    widget_set_prop_int(note, "style:normal:font_size", 16);
    return 0;
}

/* Most Played's row: the ranked list from that track, as a library (not folder) queue. */
static int mp_play(void *ctx, void *event) {
    play_folder(st.mp_list, (int)(long)ctx, 0xf001);
    return 0;
}

/* The PLAYS_TOP most played of songs, their indices in idx, most played first and, among equal
 * counts, most recently counted first (the table keeps that order); returns how many. */
static int mp_rank(void *songs, int *idx, unsigned *score) {
    int top = 0, size = (int)deque_size(songs);
    plays_load();
    for (int i = 0; i < size; i++) {
        unsigned key = listen_key(deque_at(songs, i)), c = 0;
        for (int j = 0; j < PLAYS_SLOTS && !c; j++)
            if (st.plays[j].key == key) c = st.plays[j].n * PLAYS_SLOTS + PLAYS_SLOTS - j;
        if (!c || (top == PLAYS_TOP && score[top - 1] >= c)) continue;
        int at = top < PLAYS_TOP ? top++ : PLAYS_TOP - 1;
        for (; at > 0 && score[at - 1] < c; at--) score[at] = score[at - 1], idx[at] = idx[at - 1];
        score[at] = c;
        idx[at] = i;
    }
    return top;
}

/* Coverflow's Most Played order: every counted song's listens added to sum[album_of(song)], the
 * album's index in Coverflow's list (-1 none). One pass over the library. */
void album_plays(int (*album_of)(void *), unsigned *sum) {
    int n;
    void *songs = staged(songs_unsorted, (void *)1, &n);
    plays_load();
    for (unsigned i = 0; i < deque_size(songs); i++) {
        void *r = deque_at(songs, i);
        unsigned key = listen_key(r), c = 0;
        for (int j = 0; j < PLAYS_SLOTS && !c; j++)
            if (st.plays[j].key == key) c = st.plays[j].n;
        int a = c ? album_of(r) : -1;
        if (a >= 0) sum[a] += c;
    }
    deque_destroy(songs);
}

/* Where key is among Most Played's tracks, -1 when not (or none are kept). */
static int mp_find(unsigned key) {
    for (unsigned i = 0; st.mp_list && i < deque_size(st.mp_list); i++)
        if (listen_key(deque_at(st.mp_list, i)) == key) return (int)i;
    return -1;
}

/* navigation.c's play_count: r's play counted (key), taking evicted's slot (0 for none). Most
 * Played's kept tracks take a newly counted song, unless its page shows them (its rows and song
 * menu match the deque); one that loses its count may have hidden the next best, which only the
 * whole library knows. */
void mp_counted(void *r, unsigned key, unsigned evicted) {
    if (evicted && mp_find(evicted) >= 0)
        ll.mp_gen = ~library_gen;
    else if (st.mp_list && mp_find(key) < 0) {
        if (!st.mp_page && deque_size(st.mp_list) < 2 * PLAYS_TOP)
            _deque_push_back(st.mp_list, r);
        else
            ll.mp_gen = ~library_gen;
    }
}

static int mp_closed(void *ctx, void *event) {
    st.mp_page = 0;
    return 0;
}

/* Most Played: a page of the PLAYS_TOP most played songs of the library. The whole library is read
 * only when nothing is kept; otherwise the kept tracks are ranked again.
 * ponytail: the first open after boot or a library change still reads every song, and a song first
 * counted from stock's folder browser shows without its artist until then (no library tags). */
int most_played(void *ctx, void *event) {
    if (st.mp_page || !(st.mp_page = page_open("mostplayed_page", mp_closed, page_keyup))) return 0;
    int n, idx[PLAYS_TOP];
    unsigned score[PLAYS_TOP], gen = library_gen;
    void *from = st.mp_list;
    if (!from || ll.mp_gen != gen) {
        from = staged(songs_unsorted, (void *)1, &n);
        ll.mp_gen = gen;
    }
    int top = mp_rank(from, idx, score);
    void *ranked = _create_deque("stSongInfo");
    deque_init(ranked);
    for (int i = 0; i < top; i++) _deque_push_back(ranked, deque_at(from, idx[i]));
    if (st.mp_list && st.mp_list != from) deque_destroy(st.mp_list);
    deque_destroy(from);
    st.mp_list = ranked;
    void *view = page_list_or(st.mp_page, st.mp_page, "Most Played", "No plays yet", top, 64);
    char name[512], detail[300];
    for (int i = 0; i < top; i++) { /* the artist, if tagged, and the play count, under the title */
        void *t = deque_at(st.mp_list, (unsigned)i);
        const char *artist = P(t, REC_ARTIST);
        unsigned plays = (score[i] - 1) / PLAYS_SLOTS, a = artist && *artist;
        tk_snprintf(detail, sizeof detail, a ? "%s · %u play%s" : "%s%u play%s", a ? artist : "",
                    plays, plays == 1 ? "" : "s");
        page_row_detail(view, i, track_name(name, sizeof name, t), detail, mp_play);
    }
    return 0;
}

/* Songs on the card, played from it as a folder queue, without Wi-Fi: Tidal Downloads
 * (docs/internals.md#tidal-cache), Streaming's row listing tidal.c's complete downloads, and an
 * add-on's local rows (songs_open: q2-deezer's downloads). A folder lists its songs newest first,
 * or, an album's (q2-deezer's Artist/Album folders, their files "01 - Title [id].flac"), by name:
 * in track order. ponytail: newest download first, not last played; demo imports no utime to touch
 * a replay. */
typedef struct {
    long mtime;
    void *r;
} td_row;
static int td_newer(const void *a, const void *b) {
    long x = ((const td_row *)a)->mtime, y = ((const td_row *)b)->mtime;
    return (y > x) - (y < x);
}
static int td_named(const void *a, const void *b) {
    const char *x = P(((const td_row *)a)->r, REC_NAME), *y = P(((const td_row *)b)->r, REC_NAME);
    return strcmp(x ? x : "", y ? y : "");
}
static int td_play(void *ctx, void *event) {
    void *t = deque_at(ll.td_list, (unsigned)(long)ctx);
    if (t && !access(P(t, REC_PATH), 0)) play_folder(ll.td_list, (int)(long)ctx, 0xf001);
    return 0;
}
/* dir's songs, newest first (or, album, by name), a deque the caller destroys. */
static void *card_songs(const char *dir, int album) {
    int n = 0;
    void *rows = staged(directory, (void *)dir, &n);
    unsigned size = deque_size(rows), info[36];
    td_row *v = calloc(size + 1, sizeof *v);
    n = 0;
    for (unsigned i = 0; v && i < size; i++) { /* songs only: .tmp, .part, folders aren't audio */
        void *r = deque_at(rows, i);
        if (I(r, REC_TYPE) == 8 && P(r, REC_PATH) && !__xstat(3, P(r, REC_PATH), info))
            v[n++] = (td_row){ (long)info[16], r };
    }
    if (n) qsort(v, (unsigned)n, sizeof *v, album ? td_named : td_newer);
    void *songs = _create_deque("stSongInfo");
    deque_init(songs);
    for (int i = 0; i < n; i++) _deque_push_back(songs, v[i].r);
    free(v);
    deque_destroy(rows);
    return songs;
}
/* A card app's stream folder's songs in play order (addon.c stream_poll), a deque the caller
 * destroys. */
void *app_album_songs(const char *dir) { return card_songs(dir, 1); }

/* A song's label from its file: Tidal's after its "<id>-<quality> ", a card app's before a
 * trailing " [<id>]" (q2-deezer's, as OpenDeezer names them), either less its extension. */
static void song_label(const char *f, int tidal, char *out, unsigned size) {
    const char *sp = tidal && f ? strstr(f, " ") : 0;
    tk_snprintf(out, size, "%s", sp ? sp + 1 : f ? f : "");
    char *dot = strrchr(out, '.'), *id;
    if (dot && dot != out) *dot = 0;
    if (!tidal && (id = strrchr(out, '[')) && id > out + 1 && id[-1] == ' ' &&
        id[strlen(id) - 1] == ']')
        id[-1] = 0;
}
static int td_show(const void *unused);
/* Clear All Downloads…, then on its confirmation Cancel or Remove (row 2: tidal_clear), each
 * through td_show, from a timer, as a row's click may not rebuild the list it is in. */
static int td_clear(void *ctx, void *event) {
    if (ll.td_asking && (long)ctx == 2) tidal_clear();
    ll.td_asking = !ll.td_asking;
    rearm(&ll.td_timer, td_show, 0);
    return 0;
}
/* The page's songs, read again; Tidal's end with Clear All Downloads…, as Spotify's Storage
 * Management does. Returns the list. */
static void *songs_rows(const char *dir, const char *title, int tidal, int album) {
    if (ll.td_list) deque_destroy(ll.td_list);
    ll.td_list = card_songs(dir, album);
    int n = (int)deque_size(ll.td_list);
    void *view =
        page_list_or(ll.td_page, ll.td_page, title, "No downloads yet", n + (tidal && n), 48);
    char name[256];
    for (int i = 0; i < n; i++) {
        song_label(P(deque_at(ll.td_list, (unsigned)i), REC_NAME), tidal, name, sizeof name);
        page_row_detail(view, i, name, 0, td_play);
    }
    if (tidal && n) page_row_detail(view, n, "Clear All Downloads…", 0, td_clear);
    return view;
}
/* Clear All Downloads' confirmation in the list's place, as Spotify's Remove Downloads?, on Cancel;
 * or the list again, on its Clear row. */
static int td_show(const void *unused) {
    ll.td_timer = 0;
    if (!ll.td_page) return 0;
    unsigned n = deque_size(ll.td_list);
    if (!ll.td_asking) {
        void *view = songs_rows(TIDAL_DIR, "Tidal Downloads", 1, 0);
        if ((n = deque_size(ll.td_list))) ringnav_select(view, (int)n, (int)n + 1);
        return 0;
    }
    char note[32];
    tk_snprintf(note, sizeof note, n == 1 ? "%u track" : "%u tracks", n);
    void *view = page_list(ll.td_page, ll.td_page, 0, "Remove Downloads?", 3, 64);
    page_row_detail(view, 0, note, "Keeps settings", 0);
    page_row_detail(view, 1, "Cancel", "", td_clear);
    page_row_detail(view, 2, "Remove", "", td_clear);
    ringnav_select(view, 1, 3);
    return 0;
}
static int td_closed(void *ctx, void *event) {
    ll.td_page = 0;
    ll.td_asking = 0;
    stop_timer(&ll.td_timer);
    if (ll.td_list) deque_destroy(ll.td_list);
    ll.td_list = 0;
    return 0;
}
/* Return on the confirmation is its Cancel. */
static int td_keyup(void *ctx, void *event) {
    if (!ll.td_asking || I(event, EVENT_KEY) != KEY_RETURN) return page_keyup(ctx, event);
    td_clear((void *)1, event);
    return STOP;
}
static int songs_page(const char *window, const char *dir, const char *title, int tidal,
                      int album) {
    if (ll.td_page || !(ll.td_page = page_open(window, td_closed, tidal ? td_keyup : page_keyup)))
        return 0;
    songs_rows(dir, title, tidal, album);
    return 0;
}
int tidal_downloads(void *ctx, void *event) {
    (void)ctx, (void)event;
    return songs_page("tidal_downloads", TIDAL_DIR, "Tidal Downloads", 1, 0);
}
/* A card app's local row (addon.c): a folder ("/" last) lists its songs; a song plays its folder
 * from it, as Tidal Downloads does. album: the folder is an album's, listed and played in track
 * order. */
int songs_open(const char *path, const char *title, int album) {
    char dir[256];
    unsigned k = strlen(path);
    if (!k || k >= sizeof dir) return 0;
    tk_snprintf(dir, sizeof dir, "%s", path);
    if (dir[k - 1] == '/') {
        if (k > 1) dir[k - 1] = 0;
        return songs_page("app_songs", dir, title, 0, album);
    }
    char *slash = strrchr(dir, '/');
    if (!slash || slash == dir) return 0;
    *slash = 0;
    void *songs = card_songs(dir, album);
    int at = -1;
    for (unsigned i = 0; at < 0 && i < deque_size(songs); i++) {
        const char *p = P(deque_at(songs, i), REC_PATH);
        if (p && !strcmp(p, path)) at = (int)i;
    }
    if (at >= 0)
        play_folder(songs, at, 0xf001); /* playing_page copies the queue at once */
    else
        toast("It isn't on the card any more");
    deque_destroy(songs);
    return 0;
}

/* Era (docs/internals.md#era): a row per decade with dated music, oldest first, and a decade's
 * albums by year. An album's year is its tracks' latest, as Coverflow's Year sort; undated albums
 * and the untagged have none. ponytail: decades only, no year level or covers, and the album rows
 * have no hold menu; a year page, or the decade as a scoped Coverflow, if asked for. */
#define ERA_DECADES 32
#define ERA_ALBUMS                                                                                 \
    "select album,ifnull(nullif(albumsonger,''),songer)," ALBUM_YEAR                               \
    " as release_year," ALBUM_TRACK " from songtable "                                             \
    "where " MUSIC_WHERE " and ifnull(album,'')<>''" ALBUM_GROUP
typedef struct {
    char *album, *artist, *path;
    int year;
} era_t;
static struct {
    void *page, *albums; /* the decades page, a decade's page */
    int decade[ERA_DECADES], count[ERA_DECADES], n;
    era_t *v; /* the open decade's albums, oldest first, room for cap */
    unsigned m, cap;
} era __attribute__((section(".scratch")));

static int era_sql(const char *sql, int (*row)(void *, int, char **, char **), void *ctx) {
    void *db = 0;
    /* Era uses SQLite directly rather than the stock library query wrappers. */
    char *expanded = library_scope_query(sql); /* none: the whole library, as library_query */
    /* Keep a full-library grouping query from competing with the player's song records for RAM. */
    int bad = sqlite3_open("/mnt/data/database.db", &db) ||
              sqlite3_exec(db, "PRAGMA cache_size=64;PRAGMA temp_store=FILE", 0, 0, 0) ||
              sqlite3_exec(db, expanded ? expanded : sql, row, ctx, 0);
    if (db) sqlite3_close(db);
    free(expanded);
    return !bad;
}
/* Home pane's counts: sql's one number, -1 when it can't be had. On coverflow.c count_worker's
 * thread: era_sql opens its own connection, and the device's libsqlite3 is THREADSAFE=1. */
static int count_row(void *ctx, int n, char **v, char **c) {
    if (n == 1 && v[0]) *(int *)ctx = atoi(v[0]);
    return 0;
}
int library_count(const char *sql) {
    int n = 0;
    return era_sql(sql, count_row, &n) ? n : -1;
}
static int era_decade_row(void *ctx, int n, char **v, char **c) {
    if (n == 2 && v[0] && v[1] && era.n < ERA_DECADES)
        era.decade[era.n] = atoi(v[0]), era.count[era.n++] = atoi(v[1]);
    return 0;
}
static int era_album_row(void *ctx, int n, char **v, char **c) {
    if (n != 4 || !v[0] || !v[2] || !v[3] || era.m == era.cap) return 0;
    era_t *e = &era.v[era.m];
    e->album = strdup(v[0]), e->artist = strdup(v[1] ? v[1] : ""), e->path = strdup(v[3]);
    e->year = atoi(v[2]);
    if (!e->album || !e->artist || !e->path) {
        free(e->album), free(e->artist), free(e->path);
        return 1;
    }
    ++era.m;
    return 0;
}
static void era_free(void) {
    for (unsigned i = 0; i < era.m; ++i)
        free(era.v[i].album), free(era.v[i].artist), free(era.v[i].path);
    free(era.v);
    era.v = 0, era.m = era.cap = 0;
}
static int era_closed(void *ctx, void *event) {
    if (ctx == era.page) era.page = 0;
    if (ctx == era.albums) era.albums = 0, era_free();
    return 0;
}
/* "60s" to "90s", then "2000s" on. */
static const char *era_name(char *buf, int decade) {
    tk_snprintf(buf, 16, decade < 2000 ? "%02ds" : "%ds", decade < 2000 ? decade % 100 : decade);
    return buf;
}

/* Album i's record: the album name's songs, the one at its path. *songs holds it, the caller's;
 * *n is the query's count, -1 when it failed. */
static int era_by_name(void *name) { return getMusicByAlbum(name); }
static void *era_record(unsigned i, void **songs, int *n) {
    *songs = staged_music(era_by_name, era.v[i].album, n);
    for (unsigned k = 0; k < deque_size(*songs); ++k)
        if (!strcmp(P(deque_at(*songs, k), REC_PATH), era.v[i].path)) return deque_at(*songs, k);
    return 0;
}
/* Every album's tracks, in the page's order, each in album order; 0 if a query failed.
 * ponytail: two queries an album, as Coverflow's All albums; one decade query split back into
 * albums if a large decade drags. */
static void *era_songs(void) {
    void *all = _create_deque("stSongInfo");
    deque_init(all);
    for (unsigned i = 0; i < era.m; ++i) {
        int n;
        void *songs, *r = era_record(i, &songs, &n);
        if (n < 0) {
            deque_destroy(songs), deque_destroy(all);
            return 0;
        }
        void *t = r ? coverflow_album_tracks(r) : 0;
        for (unsigned j = 0; t && j < deque_size(t); ++j) _deque_push_back(all, deque_at(t, j));
        if (t) deque_destroy(t);
        deque_destroy(songs);
    }
    return all;
}
/* Shuffle's songs: one query for the decade's files, then the library filtered by them; its
 * order is about to be shuffled, so era_songs' two queries an album would only cost time. */
static struct {
    unsigned *v, n, cap;
} era_keys __attribute__((section(".scratch")));
static int era_key_row(void *ctx, int n, char **v, char **c) {
    if (n != 1 || !v[0]) return 0;
    if (era_keys.n == era_keys.cap) {
        unsigned cap = era_keys.cap ? era_keys.cap * 2 : 1024, *p = calloc(cap, sizeof *p);
        if (!p) return 1;
        if (era_keys.n) memcpy(p, era_keys.v, era_keys.n * sizeof *p);
        free(era_keys.v);
        era_keys.v = p, era_keys.cap = cap;
    }
    era_keys.v[era_keys.n++] = (unsigned)atoi(v[0]);
    return 0;
}
static int era_key_cmp(const void *a, const void *b) {
    unsigned x = *(const unsigned *)a, y = *(const unsigned *)b;
    return x < y ? -1 : x > y;
}
static void *era_shuffle_songs(void) { /* 0 if the query failed */
    char sql[2048];
    int from = era.m ? era.v[0].year / 10 * 10 : 0, n;
    /* The songs of ERA_ALBUMS' groups dated in the decade, matched on ALBUM_GROUP's keys. */
    tk_snprintf(
        sql, sizeof sql,
        "%s from songtable s join (select album a,ifnull(albumsonger,'') b,case "
        "ifnull(albumsonger,'') when '' then rtrim(fileurl,replace(fileurl,'/','')) end d,"
        "%s as y from songtable where %s and ifnull(album,'')<>''%s having y between %d and %d) g "
        "on s.album=g.a COLLATE NOCASE and ifnull(s.albumsonger,'')=g.b COLLATE NOCASE and "
        "(g.d is null or rtrim(s.fileurl,replace(s.fileurl,'/',''))=g.d)",
        "select distinct s.id", ALBUM_YEAR, MUSIC_WHERE, ALBUM_GROUP, from, from + 9);
    era_keys.n = 0;
    void *out = era.m && !era_sql(sql, era_key_row, 0) ? 0 : _create_deque("stSongInfo");
    if (out) deque_init(out);
    if (out && era_keys.n) {
        qsort(era_keys.v, era_keys.n, sizeof *era_keys.v, era_key_cmp);
        void *all = staged(songs_unsorted, (void *)1, &n);
        for (unsigned i = 0; i < deque_size(all); ++i) {
            unsigned key = (unsigned)I(deque_at(all, i), REC_ID), lo = 0, hi = era_keys.n;
            while (lo < hi) /* the first key not below this one */
                if (era_keys.v[(lo + hi) / 2] < key)
                    lo = (lo + hi) / 2 + 1;
                else
                    hi = (lo + hi) / 2;
            if (lo < era_keys.n && era_keys.v[lo] == key) _deque_push_back(out, deque_at(all, i));
        }
        deque_destroy(all);
    }
    free(era_keys.v);
    era_keys.v = 0, era_keys.n = era_keys.cap = 0;
    return out;
}
/* Play All from the first song as a library queue, as Coverflow's All albums; Shuffle is Shuffle
 * Songs over them; an album opens its tracks in Coverflow. */
static int era_pick(void *ctx, void *event) {
    int i = (int)(long)ctx;
    if (i < 2) {
        void *all = i ? era_shuffle_songs() : era_songs();
        if (!all) {
            toast("Era unavailable"); /* as a decade's albums: not a card that's away */
            return 0;
        }
        if (!deque_size(all) || access(P(deque_at(all, 0), REC_PATH), 0))
            toast("Storage unavailable");
        else if (i)
            shuffle_play(all);
        else
            play_folder(all, 0, 0xf001);
        deque_destroy(all);
        return 0;
    }
    int n = 0;
    void *songs, *r = (unsigned)(i - 2) < era.m ? era_record((unsigned)(i - 2), &songs, &n) : 0;
    if (!r || !coverflow_goto_album(r)) toast(n < 0 ? "Era unavailable" : "Album not found");
    if ((unsigned)(i - 2) < era.m) deque_destroy(songs);
    return 0;
}
static int era_open_decade(void *ctx, void *event) {
    int k = (int)(long)ctx, from = era.decade[k];
    if (era.albums) return 0;
    char sql[1024], name[16], detail[300];
    tk_snprintf(
        sql, sizeof sql, /* MUSIC_WHERE's like patterns hold '%': an argument, not the format */
        "%s having release_year between %d and %d order by release_year,album COLLATE NOCASE",
        ERA_ALBUMS, from, from + 9);
    era.v =
        calloc((unsigned)era.count[k] + 1, sizeof *era.v); /* the decade's count, from era_menu */
    era.cap = era.v ? (unsigned)era.count[k] : 0;
    if (!era.v || !era_sql(sql, era_album_row, 0)) { /* as era_menu: not a decade of no albums */
        era_free();
        toast("Era unavailable");
        return 0;
    }
    if (!(era.albums = page_open("era_albums_page", era_closed, page_keyup))) {
        era_free();
        return 0;
    }
    void *view = page_list(era.albums, era.albums, 0, era_name(name, from), (int)era.m + 2, 64);
    tk_snprintf(detail, sizeof detail, era.m == 1 ? "%u album" : "%u albums", era.m);
    page_row_detail(view, 0, "Play All", detail, era_pick);
    page_row_detail(view, 1, "Shuffle", "Every song, shuffled", era_pick);
    for (unsigned i = 0; i < era.m; ++i) {
        tk_snprintf(detail, sizeof detail, *era.v[i].artist ? "%s \xe2\x80\xa2 %d" : "%s%d",
                    era.v[i].artist, era.v[i].year);
        page_row_detail_into(view, (int)i + 2, era.v[i].album, detail, era_pick);
    }
    return 0;
}
int era_menu(void *ctx, void *event) {
    if (era.page) return 0;
    era.n = 0;
    if (!era_sql("select year/10*10,count(*) from (select " ALBUM_YEAR
                 " year from songtable where " MUSIC_WHERE " and ifnull(album,'')<>''" ALBUM_GROUP
                 ") where year>0 group by 1 order by 1",
                 era_decade_row, 0)) {
        toast("Era unavailable");
        return 0;
    }
    if (!era.n) {
        toast("No dated albums yet");
        return 0;
    }
    if (!(era.page = page_open("era_page", era_closed, page_keyup))) return 0;
    void *view = page_list(era.page, era.page, 0, "Era", era.n, 64);
    char name[16], detail[32];
    for (int i = 0; i < era.n; ++i) {
        tk_snprintf(detail, sizeof detail, era.count[i] == 1 ? "%d album" : "%d albums",
                    era.count[i]);
        page_row_detail_into(view, i, era_name(name, era.decade[i]), detail, era_open_decade);
    }
    return 0;
}

/* Long Songs (docs/internals.md#long-songs): the library's songs over LONG_SONG_S, longest first.
 * The database keeps no length, so each file's is probed once, through stock's TagLib, on a worker
 * thread, and kept in LENGTHS_FILE by path hash: {key, seconds} pairs sorted by key, 0 seconds for
 * a file TagLib can't read. A CUE track with both ends needs no probe. */
#define LENGTHS_FILE "/mnt/data/ringnav-lengths"
typedef struct {
    unsigned key, sec;
} length_t;
static struct {
    length_t *tab, *jobs; /* the cache, sorted; the files to probe, sec filled as done */
    char **paths;         /* the jobs' files, the worker's own copies */
    unsigned n, read, timer;
    unsigned long thread;
    int total, running;
    volatile int done, cancel;
    void *songs, *title; /* the library, while its lengths are probed, and the count's title */
} ls __attribute__((section(".scratch")));

static void lengths_read(void) {
    if (ls.read) return;
    unsigned size;
    length_t *tab = read_all(LENGTHS_FILE, ~0u, &size);
    ls.read = tab || file_size(LENGTHS_FILE) <=
                         0; /* unread, not empty: not saved over, read again next time */
    ls.n = size / sizeof(length_t);
    if (!ls.n) free(tab), tab = 0;
    ls.tab = tab;
}

/* key's seconds, -1 when never probed. */
static int length_of(unsigned key) {
    unsigned lo = 0, hi = ls.n;
    while (lo < hi) {
        unsigned mid = lo + (hi - lo) / 2;
        if (ls.tab[mid].key < key)
            lo = mid + 1;
        else
            hi = mid;
    }
    return lo < ls.n && ls.tab[lo].key == key ? (int)ls.tab[lo].sec : -1;
}

/* ponytail: a 32-bit path hash; two files that collide share a length (~1% across 10k files). */
static unsigned file_key(void *r) { return fnv(FNV_SEED, P(r, REC_PATH)); }

/* r's seconds: a CUE track's from its ends (to the file's end for the last), else the file's;
 * -1 when the file was never probed. */
static int song_length(void *r) {
    int start = I(r, REC_CUE_START), end = I(r, REC_CUE_END), len;
    if (end > start) return end - start;
    len = length_of(file_key(r));
    return len < 0 ? -1 : len > start ? len - start : 0;
}

static int by_key(const void *a, const void *b) {
    unsigned x = ((const length_t *)a)->key, y = ((const length_t *)b)->key;
    return (x > y) - (x < y);
}

/* AudioProperties::length(), as libtag_c's taglib_audioproperties_length calls it: vtable +0xc. */
static void *ls_worker(void *unused) {
    while (ls.done < ls.total && !ls.cancel) {
        /* ponytail: an oversized Ogg header (ogg_tags) would cost TagLib ~4 times it; no length */
        void *f = ogg_tags(ls.paths[ls.done], 0) ? 0 : taglib_file_new(ls.paths[ls.done], 1);
        void *p = f ? taglib_file_audioproperties(f) : (void *)0;
        int sec = p ? ((int (*)(void *))(*(void ***)p)[3])(p) : 0;
        ls.jobs[ls.done].sec = sec > 0 ? (unsigned)sec : 0;
        if (f) taglib_file_free(f);
        ++ls.done;
    }
    return 0;
}

/* The worker stopped, what it probed merged into the cache and saved, its jobs freed. */
static void ls_stop(void) {
    worker_stop(ls.thread, &ls.running, &ls.cancel, &ls.timer);
    unsigned n = ls.n + (unsigned)ls.done;
    length_t *tab = calloc(n + 1, sizeof *tab);
    if (tab && ls.done) {
        if (ls.n) memcpy(tab, ls.tab, ls.n * sizeof *tab);
        memcpy(tab + ls.n, ls.jobs, (unsigned)ls.done * sizeof *tab);
        qsort(tab, n, sizeof *tab, by_key);
        unsigned k = 0; /* a file probed twice (two CUE tracks to its end) is kept once */
        for (unsigned i = 0; i < n; i++)
            if (!k || tab[i].key != tab[k - 1].key) tab[k++] = tab[i];
        n = k;
        free(ls.tab);
        ls.tab = tab;
        ls.n = n;
        if (ls.read) blob_io(LENGTHS_FILE, LENGTHS_FILE ".tmp", ls.tab, ls.n * sizeof *ls.tab, 1);
    } else
        free(tab);
    for (int i = 0; i < ls.total; i++) free(ls.paths[i]);
    free(ls.paths);
    free(ls.jobs);
    ls.paths = 0;
    ls.jobs = 0;
    ls.total = ls.done = ls.cancel = 0;
}

static int by_length(const void *a, const void *b) {
    unsigned x = ((const length_t *)a)->sec, y = ((const length_t *)b)->sec;
    return (x < y) - (x > y);
}

/* The page's list: the songs over LONG_SONG_S, longest first, each row's artist and length under
 * its title, a row playing the list from itself as Most Played's. */
static void ls_show(void) {
    void *songs = ls.songs, *list = _create_deque("stSongInfo");
    unsigned size = deque_size(songs), n = 0;
    length_t *v = calloc(size + 1, sizeof *v); /* key: the song's index */
    deque_init(list);
    for (unsigned i = 0; v && i < size; i++) {
        int sec = song_length(deque_at(songs, i));
        if (sec > LONG_SONG_S) v[n].key = i, v[n++].sec = (unsigned)sec;
    }
    if (n) qsort(v, n, sizeof *v, by_length);
    for (unsigned i = 0; i < n; i++) _deque_push_back(list, deque_at(songs, v[i].key));
    st.rt_list = list;
    void *view = page_list(st.rt_page, st.rt_page, 0, "Long Songs", n ? (int)n : 1, 64);
    char name[512], detail[300], t[16];
    for (unsigned i = 0; i < n; i++) {
        void *r = deque_at(list, i);
        const char *artist = P(r, REC_ARTIST);
        toolsTimeItoa(t, (int)v[i].sec);
        tk_snprintf(detail, sizeof detail, artist && *artist ? "%s · %s" : "%s%s",
                    artist && *artist ? artist : "", t);
        page_row_detail(view, (int)i, track_name(name, sizeof name, r), detail, rt_play);
    }
    if (!n) page_row_detail(view, 0, "No songs over 10 minutes", 0, 0);
    free(v);
    deque_destroy(songs);
    ls.songs = 0;
}

/* The page gone: what was probed is kept. Its list is ratings.c's slot, whose rows play it
 * (rt_play). */
static int ls_page_closed(void *ctx, void *event) {
    rt_closed(ctx, event);
    ls_stop();
    if (ls.songs) deque_destroy(ls.songs);
    ls.songs = 0;
    return 0;
}

static int ls_done(const void *unused) {
    ls.timer = 0;
    ls_stop();
    if (st.rt_page && ls.songs) ls_show();
    return 0;
}

static int ls_cancel(void *ctx, void *event) {
    ls.cancel = 1; /* the poll shows what is known */
    return 0;
}

static int ls_poll(const void *unused) {
    if (ls.done >= ls.total || ls.cancel) return ls_done(0);
    char t[64];
    tk_snprintf(t, sizeof t, "Measuring songs\xe2\x80\xa6 %d/%d", ls.done, ls.total);
    page_retitle(st.rt_page, ls.title, t);
    return 8; /* RET_REPEAT */
}

/* Cover Flow's tracks panel (coverflow.c): r's seconds from the cache, -1 when never probed. */
int song_seconds(void *r) {
    lengths_read();
    return song_length(r);
}

/* Long Songs' row: the files never probed are probed first, with their count and Cancel, which
 * keeps what is done; the page closing does the same. */
int long_songs(void *ctx, void *event) {
    if (st.rt_page || !(st.rt_page = page_open("longsongs_page", ls_page_closed, page_keyup)))
        return 0;
    int n;
    lengths_read();
    ls.songs = staged(songs_unsorted, (void *)1, &n);
    unsigned size = deque_size(ls.songs);
    ls.jobs = calloc(size + 1, sizeof *ls.jobs);
    ls.paths = calloc(size + 1, sizeof *ls.paths);
    for (unsigned i = 0; ls.jobs && ls.paths && i < size; i++) {
        void *r = deque_at(ls.songs, i);
        unsigned key = file_key(r);
        if (song_length(r) < 0 && P(r, REC_PATH) && (ls.paths[ls.total] = strdup(P(r, REC_PATH))))
            ls.jobs[ls.total++].key = key;
    }
    if (ls.total && !pthread_create(&ls.thread, 0, ls_worker, 0)) {
        ls.running = 1;
        page_row_detail(
            page_list(st.rt_page, st.rt_page, &ls.title, "Measuring songs\xe2\x80\xa6", 1, 48), 0,
            "Cancel", 0, ls_cancel);
        ls.timer = timer_add(ls_poll, (void *)0, 250);
    } else {
        ls_stop();
        ls_show();
    }
    return 0;
}
