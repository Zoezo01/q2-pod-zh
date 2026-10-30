/* Coverflow (docs/internals.md): the stock Local Music albums on a stock slide_menu, the way
 * PictureFlow reads Rockbox's database. No tag reads: albums and tracks come from the stock library
 * queries, and the only thing Coverflow owns is a thumbnail cache, built on a modal screen by one
 * pthread that touches only files, the two stock art locks and the volatile counters below. */
#include "offsets.inc"
#include "peq.h"
#ifndef PEQ_HOST
#include "stock.h"
#endif
#include "playback.h"

/* On the card, beside stock's own cover cache (/mnt/mmc/.sldp). */
#define ART_DIR PEQ_ROOT "/mnt/mmc/.coverflow"
#define ART_SIZE 160
#define LAST_ALBUM ART_DIR "/album" /* the centre album's key, so a reboot opens on it */
#if IPOD /* iPod insets its text from the rounded glass (offsets.inc CF_*); Stock keeps its layout */
#define CF_X CF_EDGE
#define CF_W (375 - 2 * CF_EDGE)
#define CF_ROW_X CF_X /* one text column throughout */
#define CF_ROW_W CF_W
#else
#define CF_X 8
#define CF_W 359
#define CF_ROW_X 12
#define CF_ROW_W 350
#endif
#define MIN_FREE_MB 16 /* no new cache files below this much free space on the card */
#define ART_NEAR 3 /* real art only this many covers either side, like PictureFlow's cache */
#define HOME_PLACEHOLDER "default_album_home" /* Classic Home's, at 290px (stock's: 110) */
enum { ARTIST_ALBUMS = 1, RATED_ALBUMS, ALBUM_TRACKS }; /* cf.scoped */
/* after the albums: an artist's All albums (two or more), the rated albums' Sort; none after the
 * library's, whose Sort and Refresh are Display settings' Coverflow Sort and Refresh Coverflow
 * (coverflow_sort_step, coverflow_refresh_art) */
#define CARDS (cf.scoped == ARTIST_ALBUMS ? deque_size(cf.albums) > 1 : cf.scoped ? 1 : 0)

extern int stock_home_trampoline(void *win, void *ctx), stock_scan_all_trampoline(void *, void *),
    stock_scan_folder_trampoline(void *, void *), stock_delete_song_trampoline(void *, void *);

enum { PREPARING, COVERS, TRACKS };
typedef struct {
    char *track; /* the album's first track, copied so the thread never reads stock deques */
    unsigned key;
    int shared;  /* another album has a track in this folder, so its cover.jpg may be theirs */
} job_t;
static struct {
    void *page, *body, *covers, *slide, *name, *artist, *title;
    void *albums, *tracks; /* our copies of the stock query results; albums in the Sort's order */
    void *stock;           /* the albums in stock order, which albums is, or is sorted from */
    int sort, sort_read;   /* the Sort card's order (SORT_*), from SORT_FILE once */
    int year_old, artists; /* the Year order and Artists settings albums was sorted in */
    int on_sort;           /* the next load centres the Sort card */
    int resort;            /* Display settings changed the library's order: the next load reads it */
    job_t *jobs;
    unsigned long thread;
    unsigned timer, title_timer; /* title_timer: the captions follow the centre cover mid-move */
    int titled;                  /* the album the captions name, + 1 */
    unsigned saved_album, albums_gen; /* albums_gen: library_gen when albums was queried */
    int screen, album, running, text_albums;
    volatile int done, total, cancel;
    /* Scoped (ARTIST_ALBUMS, RATED_ALBUMS or ALBUM_TRACKS, else 0): an artist's albums
     * (coverflow_artist), albums holding them, the rated ones (coverflow_rated), stock holding them
     * in the library's order and sort their own order, or one album's tracks alone
     * (coverflow_goto_album); all_albums, all_stock and all_sort the library's, back on close.
     * No saved album; hearts gives a rated album's. */
    int scoped, all_sort;
    void *all_albums, *all_stock;
    int (*hearts)(void *);
} cf __attribute__((section(".scratch")));

static int pick(void *ctx, void *event);

/* The album list is kept across opens until songtable changes. Only these three stock functions
 * write it; each moves the generation before and after it runs, so a list queried meanwhile is
 * never kept. The scans run on stock's scan thread. */
volatile unsigned library_gen __attribute__((section(".scratch"))); /* also Most Played's */
static int library_write(int (*stock)(void *, void *), void *a, void *b) {
    ++library_gen;
    int result = stock(a, b);
    ++library_gen;
    return result;
}
int coverflow_scan_all(void *a, void *b) { return library_write(stock_scan_all_trampoline, a, b); }
int coverflow_scan_folder(void *a, void *b) { return library_write(stock_scan_folder_trampoline, a, b); }
int coverflow_delete_song(void *a, void *b) { return library_write(stock_delete_song_trampoline, a, b); }

/* The queue menu resolves the live track list again after its dialog closes. */
void *coverflow_tracks(void *page) {
    return page == cf.page && cf.screen == TRACKS ? cf.tracks : 0;
}

/* Only a live album card, never a Sort or All albums card or a pending screen change. */
void *coverflow_album(void *page) {
    if (page != cf.page || cf.screen != COVERS || !cf.slide || cf.timer) return 0;
    unsigned i = (unsigned)I(cf.slide, SLIDE_INDEX);
    return cf.albums && i < deque_size(cf.albums) ? deque_at(cf.albums, i) : 0;
}

/* FNV-1a, shared with navigation.c */
unsigned hash_bytes(unsigned h, const unsigned char *s, unsigned n) {
    for (unsigned i = 0; i < n; ++i) h = (h ^ s[i]) * 16777619u;
    return h;
}

unsigned fnv(unsigned h, const unsigned char *s) {
    while (s && *s) h = hash_bytes(h, s++, 1);
    return h * 16777619u; /* a separator, so "ab"+"c" and "a"+"bc" differ */
}

static unsigned tags_key(const char *artist, const char *album) {
    return fnv(fnv(FNV_SEED, (const unsigned char *)artist), (const unsigned char *)album);
}

static unsigned album_key(void *r) { return tags_key(P(r, REC_ARTIST), P(r, REC_ALBUM)); }

/* An album's artist as Artists sets it: with Album Artist (artist_type 1), its album artist tag
 * when set, so a compilation reads "Various Artists" or its compiler, not its last track's artist.
 */
static const char *cover_artist(void *r) {
    const char *aa = P(r, REC_ALBUM_ARTIST);
    return I(artist_type, 0) == 1 && aa && *aa ? aa : P(r, REC_ARTIST);
}

/* Track selection uses ringnav's existing position memory, keyed by this album. */
unsigned coverflow_scope(void *page) {
    return coverflow_tracks(page) ? album_key(deque_at(cf.albums, (unsigned)cf.album)) : 0;
}

/* ART_DIR/<key>.jpg<suffix>; out holds at least 512 bytes. */
static char *art_path(char *out, unsigned key, const char *suffix) {
    tk_snprintf(out, 512, ART_DIR "/%08x.jpg%s", key, suffix);
    return out;
}
/* Asks the image cache for a cover only once its file exists: a load made before the worker wrote
 * it stays a failure, and the placeholder would stay until a reboot. url is "file://" + the path. */
static int art_exists(const char *url) { return !access(url + 7, 0); }

#ifndef ART_FFMPEG
#define ART_FFMPEG "/usr/bin/ffmpeg"
#endif

/* path's size, -1 when it can't be opened */
long file_size(const char *path) {
    void *f = fopen(path, "rb");
    long size = f && !fseek(f, 0, 2) ? ftell(f) : -1;
    if (f) fclose(f);
    return size;
}

/* Stock ffmpeg's first picture of src (an image, or an audio file's embedded one) fitted into w x h
 * as a JPEG at dst: what stock's decoder refuses (WebP, a PNG over 1 MB, a JPEG over 6 MB). Uses no
 * lock: it writes only dst.ff, then renames it onto dst. ffmpeg creates its output before decoding,
 * so a picture it can't decode leaves an empty file, even on exit 0, and a killed run a partial
 * one; either is removed and dst is left alone, as stock's caches take any file for art.
 * -xerror stops at a picture's first decode error rather than demuxing the rest of the file.
 * *exited: ffmpeg ran to its own exit (no refused fork, no kill), so a failure is the picture's.
 * ponytail: a process per cover; a resident decoder if first builds drag. */
static int ff_run(const char *src, const char *dst, int w, int h, int *exited) {
    char vf[80], tmp[1040];
    tk_snprintf(vf, sizeof(vf), "scale=%d:%d:force_original_aspect_ratio=decrease", w, h);
    tk_snprintf(tmp, sizeof(tmp), "%s.ff", dst);
    int status = -1, pid = fork();
    if (!pid) {
        execl(ART_FFMPEG, ART_FFMPEG, "-nostdin", "-v", "quiet", "-xerror", "-i", src, "-frames:v", "1",
              "-vf", vf, "-pix_fmt", "yuvj420p", "-q:v", "3", "-f", "mjpeg", "-y", tmp, (char *)0);
        child_exit(127);
    }
    while (pid > 0 && waitpid(pid, &status, 0) < 0 && *__errno_location() == 4) { /* EINTR: reap it */
    }
    *exited = !(status & 0x7f); /* WIFEXITED; not for -1 */
    if (!status && file_size(tmp) > 0 && !rename(tmp, dst)) return 1;
    unlink(tmp);
    return 0;
}

static int ff_thumb(const char *src, const char *dst, int w, int h) {
    int exited;
    return ff_run(src, dst, w, h, &exited);
}

/* stock's thumbnailer, under the lock every stock caller holds, else ffmpeg; shared with photos.c */
int thumb(const char *src, const char *dst, int w, int h) {
    pthread_mutex_lock((void *)parse_cover_mutex);
    int ok = toolsThumbSpecCover(src, dst, w, h) == 1;
    pthread_mutex_unlock((void *)parse_cover_mutex);
    return ok || (!access(src, 4) && ff_thumb(src, dst, w, h)); /* none for a missing file */
}

/* Whether another album has a track in this one's folder. ponytail: a scan of every album per
 * job, albums squared; a per-folder count if large libraries lag. */
static int folder_shared(unsigned key, const char *track) {
    const char *slash = strrchr(track, '/');
    for (unsigned i = 0; slash && i < deque_size(cf.albums); ++i) {
        void *r = deque_at(cf.albums, i);
        const char *p = P(r, REC_PATH), *s = p ? strrchr(p, '/') : 0;
        if (s && s - p == slash - track && !memcmp(p, track, s - p) && album_key(r) != key) return 1;
    }
    return 0;
}

/* The cover and folder images beside track through fit (none when shared: a folder shared with
 * other albums, whose cover.jpg would be theirs too). */
static int beside_art(const char *track, const char *dst, int w, int h, int shared,
                      int (*fit)(const char *, const char *, int, int)) {
    char src[1024];
    const char *slash = strrchr(track, '/');
    int folder = slash ? (int)(slash - track) : 0, ok = 0;
    static const char *const names[] = { "cover.jpg",  "folder.jpg",  "cover.png",
                                          "folder.png", "cover.webp", "folder.webp" };
    for (unsigned i = 0; i < sizeof(names) / sizeof(*names) && !ok && !shared; ++i) {
        snprintf(src, sizeof(src), "%.*s/%s", folder, track, names[i]);
        ok = !access(src, 4) && fit(src, dst, w, h);
    }
    return ok;
}

/* Whether Coverflow marked the album of the track the player parsed (g_play_id3_info, keyed as
 * Home's art is) empty: its build found no art for the album's track. */
static int marked_empty(const char *track) {
    const char *id3 = (const char *)g_play_id3_info;
    if (tk_strcmp(id3, track)) return 0;
    char art[512];
    return !file_size(art_path(art, tags_key(id3 + ID3_ARTIST, id3 + ID3_ALBUM), ""));
}

/* player_parsecover_thd's folder-image call, after its embedded art failed, under
 * g_playcover_mutex: what stock refuses (a PNG over 1 MB or palette/greyscale, WebP) goes to
 * ffmpeg, so Now Playing gets the art Coverflow shows. ff_thumb takes no lock, so Coverflow's
 * parse-then-play order cannot deadlock with it. The folder images cost a process only when one
 * exists; the embedded probe, a process for every track without art, is skipped for an album
 * Coverflow marked empty. ponytail: the marker is one track's verdict, so another disc's or a
 * same-named album's embedded-only WebP or large PNG shows no art here; a per-track probe cache
 * if that matters. */
int coverflow_player_cover(const char *track, const char *dst, int w, int h) {
    return toolsGetExternCover(track, dst, w, h) == 1 || beside_art(track, dst, w, h, 0, ff_thumb) ||
           (!marked_empty(track) && ff_thumb(track, dst, w, h));
}

/* parse_albumcovertask_thd's embedded-art calls (Albums grids; Folder view's folder and track rows),
 * made on stock's one cover thread under parse_cover_mutex: stock reads no Ogg picture, so an .opus
 * or .ogg track's goes to ffmpeg. The lock is dropped for ffmpeg, as the UI thread takes it for
 * Library's online thumbnails and Spotify's art: the call's paths are the thread's own, and stock's
 * fallback after it runs with the lock held again. The Albums grid's call (album) reads the folder
 * image first, which stock would only after it, so an Ogg album whose art is a cover.jpg costs no
 * process; that also orders Ogg art as Coverflow does. Stock caches only a hit, so a track ffmpeg
 * found no picture in is remembered (not one gone with the card, nor a refused fork or a killed
 * ffmpeg). ponytail: 1024 misses until a
 * reboot, oldest overwritten; a picture since added to a track shows after a reboot. */
static int grid_cover(const char *track, const char *dst, int w, int h, int album) {
    static unsigned *miss __attribute__((section(".scratch"))), n __attribute__((section(".scratch")));
    const char *dot = strrchr(track, '.');
    if (toolsGetAlbumCover(track, dst, w, h) == 1) return 1;
    if (!dot || (strcasecmp(dot, ".opus") && strcasecmp(dot, ".ogg"))) return 0;
    unsigned key = fnv(FNV_SEED, (const unsigned char *)track);
    if (!miss) miss = calloc(1024, sizeof(*miss)); /* heap, as the scratch page is nearly full */
    for (unsigned i = 0; miss && i < n && i < 1024; ++i)
        if (miss[i] == key) return 0;
    if (album && toolsGetExternCover(track, dst, w, h) == 1) return 1;
    pthread_mutex_unlock((void *)parse_cover_mutex);
    int exited, ok = ff_run(track, dst, w, h, &exited);
    pthread_mutex_lock((void *)parse_cover_mutex);
    if (!ok && exited && miss && !access(track, 4)) miss[n++ % 1024] = key;
    return ok;
}

int coverflow_grid_cover(const char *track, const char *dst, int w, int h) {
    return grid_cover(track, dst, w, h, 0);
}

int coverflow_album_cover(const char *track, const char *dst, int w, int h) {
    return grid_cover(track, dst, w, h, 1);
}

/* One album: embedded art, then beside_art, then ffmpeg's read of the embedded art, last as it
 * costs a process for every album without art; written to .tmp and renamed. An empty file marks
 * "no art", so the placeholder shows and the album is not retried until Refresh Coverflow. */
static void build_art(const job_t *j) {
    char dst[512], tmp[512];
    if (access(j->track, 0)) return; /* storage gone: no marker, the next open retries */
    art_path(dst, j->key, "");
    art_path(tmp, j->key, ".tmp");
    /* toolsGetAlbumCover goes through the shared /tmp/.tmp_picture, which stock guards with
     * either lock (parse_albumcovertask_thd, player_parsecover_thd); hold both. Stock never
     * nests them, so this order cannot deadlock. */
    pthread_mutex_lock((void *)parse_cover_mutex);
    pthread_mutex_lock((void *)g_playcover_mutex);
    int ok = toolsGetAlbumCover(j->track, tmp, ART_SIZE, ART_SIZE) == 1;
    pthread_mutex_unlock((void *)g_playcover_mutex);
    pthread_mutex_unlock((void *)parse_cover_mutex);
    ok = ok || beside_art(j->track, tmp, ART_SIZE, ART_SIZE, j->shared, thumb) ||
         ff_thumb(j->track, tmp, ART_SIZE, ART_SIZE);
    if (ok && !rename(tmp, dst)) return;
    unlink(tmp);
    if (access(j->track, 0)) return;
    void *f = fopen(dst, "w");
    if (f) fclose(f);
}

static void *worker(void *unused) {
    while (cf.done < cf.total && !cf.cancel) {
        build_art(&cf.jobs[cf.done]);
        ++cf.done;
    }
    return 0;
}

/* A running worker cancelled and joined, and its poll timer stopped; 1 when one ran. Shared with
 * photos.c and books.c. */
int worker_stop(unsigned long thread, int *running, volatile int *cancel, unsigned *timer) {
    int ran = *running;
    if (ran) {
        *cancel = 1;
        pthread_join(thread, 0);
        *running = 0;
    }
    stop_timer(timer);
    return ran;
}

/* Whether dir's file system has MIN_FREE_MB free (statfs, MIPS o32 layout: f_bsize is word 1,
 * f_bavail word 7); shared with photos.c. */
int card_space(const char *dir) {
    unsigned fs[32] = { 0 };
    return !statfs(dir, fs) && (unsigned long long)fs[7] * fs[1] >= (unsigned long long)MIN_FREE_MB << 20;
}

/* Cancel stops after the current album; finished thumbnails stay, so the next open resumes. */
static void stop(void) {
    worker_stop(cf.thread, &cf.running, &cf.cancel, &cf.timer);
    for (int i = 0; i < cf.total; ++i) free(cf.jobs[i].track);
    free(cf.jobs);
    cf.jobs = 0;
    cf.total = cf.done = 0;
}

static void fx_close(void);

static void drop(void) {
    stop();
    fx_close();
    if (cf.tracks) deque_destroy(cf.tracks);
    cf.tracks = 0;
}

static void drop_albums(void) {
    if (cf.albums && cf.albums != cf.stock) deque_destroy(cf.albums);
    if (cf.stock) deque_destroy(cf.stock);
    cf.albums = cf.stock = 0;
}

/* A stock library query's rows (*count its result) copied out of the staging deque, which is
 * restored so the query leaves no trace; shared with navigation.c's queue menu. */
void *staged(int (*query)(void *), void *arg, int *count) {
    void *dir = P(tools_pdeq_directory, 0), *save = _create_deque("stSongInfo"),
         *out = _create_deque("stSongInfo");
    deque_init_copy(save, dir);
    *count = query(arg);
    deque_init_copy(out, dir);
    deque_clear(dir);
    deque_assign(dir, save);
    deque_destroy(save);
    return out;
}

/* getAllAlbum, as load_localclass_list 0xf003 runs it, or the album's getMusicByAlbum, as its row
 * opens it. Stock order, including the trailing "Unknown Album" (id -1) row. */
static int albums(void *album) {
    return album ? getMusicByAlbum(I(album, REC_ID) == -1 ? (const char *)0 : P(album, REC_ALBUM))
                 : getAllAlbum();
}

/* p's folder length; with disc, a CD1, Disc 2 or Disk_03 folder (cd/disc/disk, case aside, an
 * optional space, _ or -, digits) is its parent's, so a multi-disc album is one. */
unsigned album_dir(const char *p, int disc) {
    const char *e = strrchr(p, '/');
    unsigned n = e ? (unsigned)(e - p) : 0, i = n;
    while (disc && i && p[i - 1] >= '0' && p[i - 1] <= '9') --i;
    if (i == n) return n;
    i -= i && (p[i - 1] == ' ' || p[i - 1] == '_' || p[i - 1] == '-');
    if (i >= 3 && p[i - 3] == '/' && !strncasecmp(p + i - 2, "cd", 2)) return i - 3;
    if (i >= 5 && p[i - 5] == '/' && !strncasecmp(p + i - 4, "dis", 3) &&
        ((p[i - 1] | 32) == 'c' || (p[i - 1] | 32) == 'k'))
        return i - 5;
    return n;
}
static int dir_cmp(void *a, void *b, int disc) {
    const char *pa = P(a, REC_PATH) ? P(a, REC_PATH) : "", *pb = P(b, REC_PATH) ? P(b, REC_PATH) : "";
    unsigned na = album_dir(pa, disc), nb = album_dir(pb, disc);
    int d = memcmp(pa, pb, na < nb ? na : nb);
    return d ? d : na < nb ? -1 : na != nb;
}
/* Albums apart, as split groups them: the name, then the album artist, else the folder (disc
 * folders as one), ASCII case aside but the folder's, so a compilation stays one album; tagged
 * names first. Shuffle Folders' folder_cmp keeps disc folders apart. Both shared with playback.c. */
int folder_cmp(void *a, void *b) { return dir_cmp(a, b, 0); }
int album_cmp(void *a, void *b) {
    const char *x = P(a, REC_ALBUM), *y = P(b, REC_ALBUM);
    int tx = x && *x, d = (y && *y) - tx;
    if (d || (tx && (d = strcasecmp(x, y)))) return d;
    x = P(a, REC_ALBUM_ARTIST), y = P(b, REC_ALBUM_ARTIST);
    int ax = x && *x, ay = y && *y;
    if (tx && (ax || ay)) return ay != ax ? ay - ax : strcasecmp(x, y);
    return dir_cmp(a, b, 1);
}
/* r's album as album_cmp tells it apart, hashed for navigation.c's album ratings: the name and the
 * album artist, else the folder (disc folders as one), ASCII case aside but the folder's; the
 * Unknown card is one album. Never 0. */
static unsigned fnv_lower(unsigned h, const char *s) {
    for (; s && *s; ++s) {
        unsigned char c = (unsigned char)*s;
        c += (unsigned char)((unsigned)(c - 'A') < 26u) * 32;
        h = hash_bytes(h, &c, 1);
    }
    return h * 16777619u;
}
unsigned album_id(void *r) {
    const char *x = P(r, REC_ALBUM), *y = P(r, REC_ALBUM_ARTIST), *p = P(r, REC_PATH) ? P(r, REC_PATH) : "";
    unsigned h = fnv_lower(FNV_SEED, x);
    if (I(r, REC_ID) == -1) h = fnv(FNV_SEED, (const unsigned char *)"?");
    else h = x && *x && y && *y ? fnv_lower(h, y)
                                : hash_bytes(h, (const unsigned char *)p, album_dir(p, 1)) * 16777619u;
    return h | !h;
}

/* getAllAlbum ends with an "Unknown Album" row (id -1) whenever any album exists, even when every
 * song has an album tag. It goes when the query its card opens, getMusicByAlbum(NULL), finds no
 * song, as navigation.c's ringnav_localclass drops it from the Albums list. */
static void drop_unknown(void) {
    unsigned n = deque_size(cf.stock);
    void *last = n ? deque_at(cf.stock, n - 1) : 0;
    if (!last || I(last, REC_ID) != -1) return;
    int found;
    void *songs = staged(albums, last, &found);
    if (!deque_size(songs)) deque_pop_back(cf.stock);
    deque_destroy(songs);
}

/* Sort, Display settings' Coverflow Sort and the rated albums' card
 * (docs/internals.md#coverflow-sort): Album is the stock order;
 * Artist sorts by artist without a leading article, as every library list does (ringnav_sort_key),
 * then year, then album; Year in Playback settings' Year order (year_sql), undated last; Recently Added by the newest song's time_create; Most Played by the
 * album's listens (navigation.c album_plays); Random by playback.c's rand_below, reshuffled whenever
 * the list is rebuilt. Ties keep the stock order, and the Unknown row stays last. Ranks come from ALBUM_SQL with another ORDER BY (toolsQueryDbTable without its name sort),
 * matched back to the rows by album_cmp. */
enum { SORT_ALBUM, SORT_ARTIST, SORT_ADDED, SORT_PLAYED, SORT_RANDOM, SORT_RATING, SORT_YEAR };
#define SORT_FILE PEQ_ROOT "/mnt/data/ringnav-coversort"
/* The rated albums' Sort: most hearts first, Album, Artist, Year, then Most Played. */
#define RATED_SORT_FILE PEQ_ROOT "/mnt/data/ringnav-ratedsort"
static const unsigned char rated_sorts[] = { SORT_RATING, SORT_ALBUM, SORT_ARTIST, SORT_YEAR,
                                             SORT_PLAYED };
/* The library's Sort: Year after Artist; saved values are the enum's. */
static const unsigned char all_sorts[] = { SORT_ALBUM, SORT_ARTIST, SORT_YEAR,
                                           SORT_ADDED, SORT_PLAYED, SORT_RANDOM };
#define ALBUM_SQL "select id,album,songer,fileurl,albumsonger from songtable" ALBUM_GROUP
#define MUSIC_ALBUM_SQL "select case when ifnull(album,'')='' then -1 else id end," \
    "case when ifnull(album,'')='' then 'Unknown Album' else album end," \
    "songer,fileurl,albumsonger from songtable where " \
    MUSIC_WHERE ALBUM_GROUP
#define SORT_SQL ALBUM_SQL " order by "
extern int ringnav_year_old(void);
/* Albums by year, Playback settings' Year order (navigation.c): newest or oldest first, undated last. */
static const char *year_sql(void) {
    return ringnav_year_old() ? SORT_SQL ALBUM_YEAR "=0," ALBUM_YEAR ",album COLLATE NOCASE"
                              : SORT_SQL ALBUM_YEAR "=0," ALBUM_YEAR " desc,album COLLATE NOCASE";
}
static const char *const sort_names[] = { "Sort: Album", "Sort: Artist", "Sort: Recently Added",
                                          "Sort: Most Played", "Sort: Random", "Sort: Rating", "Sort: Year" };
/* The order step after cf.sort (dir 1), or cf.sort itself (0), in the page's own orders; an
 * order not among them is the first (Album, or the rated albums' Rating). */
static int sort_next(int dir) {
    const unsigned char *o = cf.scoped ? rated_sorts : all_sorts;
    unsigned n = cf.scoped ? sizeof rated_sorts : sizeof all_sorts, i = 0;
    while (i < n && o[i] != cf.sort) ++i;
    return i == n ? o[0] : o[(i + (unsigned)dir) % n];
}
extern void album_plays(int (*album_of)(void *), unsigned *sum);
extern unsigned rand_below(unsigned n);
extern void ringnav_sort_key(char *s);
#define ARTIST_KEY 96

/* Unicode 16.0.0 number, punctuation and separator ranges. */
static const unsigned initial_misc[][2] = {
    { 0xa0, 0xa1 },
    { 0xa7, 0xa7 },
    { 0xab, 0xab },
    { 0xb2, 0xb3 },
    { 0xb6, 0xb7 },
    { 0xb9, 0xb9 },
    { 0xbb, 0xbf },
    { 0x37e, 0x37e },
    { 0x387, 0x387 },
    { 0x55a, 0x55f },
    { 0x589, 0x58a },
    { 0x5be, 0x5be },
    { 0x5c0, 0x5c0 },
    { 0x5c3, 0x5c3 },
    { 0x5c6, 0x5c6 },
    { 0x5f3, 0x5f4 },
    { 0x609, 0x60a },
    { 0x60c, 0x60d },
    { 0x61b, 0x61b },
    { 0x61d, 0x61f },
    { 0x660, 0x66d },
    { 0x6d4, 0x6d4 },
    { 0x6f0, 0x6f9 },
    { 0x700, 0x70d },
    { 0x7c0, 0x7c9 },
    { 0x7f7, 0x7f9 },
    { 0x830, 0x83e },
    { 0x85e, 0x85e },
    { 0x964, 0x970 },
    { 0x9e6, 0x9ef },
    { 0x9f4, 0x9f9 },
    { 0x9fd, 0x9fd },
    { 0xa66, 0xa6f },
    { 0xa76, 0xa76 },
    { 0xae6, 0xaf0 },
    { 0xb66, 0xb6f },
    { 0xb72, 0xb77 },
    { 0xbe6, 0xbf2 },
    { 0xc66, 0xc6f },
    { 0xc77, 0xc7e },
    { 0xc84, 0xc84 },
    { 0xce6, 0xcef },
    { 0xd58, 0xd5e },
    { 0xd66, 0xd78 },
    { 0xde6, 0xdef },
    { 0xdf4, 0xdf4 },
    { 0xe4f, 0xe5b },
    { 0xed0, 0xed9 },
    { 0xf04, 0xf12 },
    { 0xf14, 0xf14 },
    { 0xf20, 0xf33 },
    { 0xf3a, 0xf3d },
    { 0xf85, 0xf85 },
    { 0xfd0, 0xfd4 },
    { 0xfd9, 0xfda },
    { 0x1040, 0x104f },
    { 0x1090, 0x1099 },
    { 0x10fb, 0x10fb },
    { 0x1360, 0x137c },
    { 0x1400, 0x1400 },
    { 0x166e, 0x166e },
    { 0x1680, 0x1680 },
    { 0x169b, 0x169c },
    { 0x16eb, 0x16f0 },
    { 0x1735, 0x1736 },
    { 0x17d4, 0x17d6 },
    { 0x17d8, 0x17da },
    { 0x17e0, 0x17e9 },
    { 0x17f0, 0x17f9 },
    { 0x1800, 0x180a },
    { 0x1810, 0x1819 },
    { 0x1944, 0x194f },
    { 0x19d0, 0x19da },
    { 0x1a1e, 0x1a1f },
    { 0x1a80, 0x1a89 },
    { 0x1a90, 0x1a99 },
    { 0x1aa0, 0x1aa6 },
    { 0x1aa8, 0x1aad },
    { 0x1b4e, 0x1b60 },
    { 0x1b7d, 0x1b7f },
    { 0x1bb0, 0x1bb9 },
    { 0x1bfc, 0x1bff },
    { 0x1c3b, 0x1c49 },
    { 0x1c50, 0x1c59 },
    { 0x1c7e, 0x1c7f },
    { 0x1cc0, 0x1cc7 },
    { 0x1cd3, 0x1cd3 },
    { 0x2000, 0x200a },
    { 0x2010, 0x2029 },
    { 0x202f, 0x2043 },
    { 0x2045, 0x2051 },
    { 0x2053, 0x205f },
    { 0x2070, 0x2070 },
    { 0x2074, 0x2079 },
    { 0x207d, 0x207e },
    { 0x2080, 0x2089 },
    { 0x208d, 0x208e },
    { 0x2150, 0x2182 },
    { 0x2185, 0x2189 },
    { 0x2308, 0x230b },
    { 0x2329, 0x232a },
    { 0x2460, 0x249b },
    { 0x24ea, 0x24ff },
    { 0x2768, 0x2793 },
    { 0x27c5, 0x27c6 },
    { 0x27e6, 0x27ef },
    { 0x2983, 0x2998 },
    { 0x29d8, 0x29db },
    { 0x29fc, 0x29fd },
    { 0x2cf9, 0x2cff },
    { 0x2d70, 0x2d70 },
    { 0x2e00, 0x2e2e },
    { 0x2e30, 0x2e4f },
    { 0x2e52, 0x2e5d },
    { 0x3000, 0x3003 },
    { 0x3007, 0x3011 },
    { 0x3014, 0x301f },
    { 0x3021, 0x3029 },
    { 0x3030, 0x3030 },
    { 0x3038, 0x303a },
    { 0x303d, 0x303d },
    { 0x30a0, 0x30a0 },
    { 0x30fb, 0x30fb },
    { 0x3192, 0x3195 },
    { 0x3220, 0x3229 },
    { 0x3248, 0x324f },
    { 0x3251, 0x325f },
    { 0x3280, 0x3289 },
    { 0x32b1, 0x32bf },
    { 0xa4fe, 0xa4ff },
    { 0xa60d, 0xa60f },
    { 0xa620, 0xa629 },
    { 0xa673, 0xa673 },
    { 0xa67e, 0xa67e },
    { 0xa6e6, 0xa6ef },
    { 0xa6f2, 0xa6f7 },
    { 0xa830, 0xa835 },
    { 0xa874, 0xa877 },
    { 0xa8ce, 0xa8d9 },
    { 0xa8f8, 0xa8fa },
    { 0xa8fc, 0xa8fc },
    { 0xa900, 0xa909 },
    { 0xa92e, 0xa92f },
    { 0xa95f, 0xa95f },
    { 0xa9c1, 0xa9cd },
    { 0xa9d0, 0xa9d9 },
    { 0xa9de, 0xa9df },
    { 0xa9f0, 0xa9f9 },
    { 0xaa50, 0xaa59 },
    { 0xaa5c, 0xaa5f },
    { 0xaade, 0xaadf },
    { 0xaaf0, 0xaaf1 },
    { 0xabeb, 0xabeb },
    { 0xabf0, 0xabf9 },
    { 0xfd3e, 0xfd3f },
    { 0xfe10, 0xfe19 },
    { 0xfe30, 0xfe52 },
    { 0xfe54, 0xfe61 },
    { 0xfe63, 0xfe63 },
    { 0xfe68, 0xfe68 },
    { 0xfe6a, 0xfe6b },
    { 0xff01, 0xff03 },
    { 0xff05, 0xff0a },
    { 0xff0c, 0xff1b },
    { 0xff1f, 0xff20 },
    { 0xff3b, 0xff3d },
    { 0xff3f, 0xff3f },
    { 0xff5b, 0xff5b },
    { 0xff5d, 0xff5d },
    { 0xff5f, 0xff65 },
    { 0x10100, 0x10102 },
    { 0x10107, 0x10133 },
    { 0x10140, 0x10178 },
    { 0x1018a, 0x1018b },
    { 0x102e1, 0x102fb },
    { 0x10320, 0x10323 },
    { 0x10341, 0x10341 },
    { 0x1034a, 0x1034a },
    { 0x1039f, 0x1039f },
    { 0x103d0, 0x103d5 },
    { 0x104a0, 0x104a9 },
    { 0x1056f, 0x1056f },
    { 0x10857, 0x1085f },
    { 0x10879, 0x1087f },
    { 0x108a7, 0x108af },
    { 0x108fb, 0x108ff },
    { 0x10916, 0x1091b },
    { 0x1091f, 0x1091f },
    { 0x1093f, 0x1093f },
    { 0x109bc, 0x109bd },
    { 0x109c0, 0x109cf },
    { 0x109d2, 0x109ff },
    { 0x10a40, 0x10a48 },
    { 0x10a50, 0x10a58 },
    { 0x10a7d, 0x10a7f },
    { 0x10a9d, 0x10a9f },
    { 0x10aeb, 0x10af6 },
    { 0x10b39, 0x10b3f },
    { 0x10b58, 0x10b5f },
    { 0x10b78, 0x10b7f },
    { 0x10b99, 0x10b9c },
    { 0x10ba9, 0x10baf },
    { 0x10cfa, 0x10cff },
    { 0x10d30, 0x10d39 },
    { 0x10d40, 0x10d49 },
    { 0x10d6e, 0x10d6e },
    { 0x10e60, 0x10e7e },
    { 0x10ead, 0x10ead },
    { 0x10f1d, 0x10f26 },
    { 0x10f51, 0x10f59 },
    { 0x10f86, 0x10f89 },
    { 0x10fc5, 0x10fcb },
    { 0x11047, 0x1104d },
    { 0x11052, 0x1106f },
    { 0x110bb, 0x110bc },
    { 0x110be, 0x110c1 },
    { 0x110f0, 0x110f9 },
    { 0x11136, 0x11143 },
    { 0x11174, 0x11175 },
    { 0x111c5, 0x111c8 },
    { 0x111cd, 0x111cd },
    { 0x111d0, 0x111d9 },
    { 0x111db, 0x111db },
    { 0x111dd, 0x111df },
    { 0x111e1, 0x111f4 },
    { 0x11238, 0x1123d },
    { 0x112a9, 0x112a9 },
    { 0x112f0, 0x112f9 },
    { 0x113d4, 0x113d5 },
    { 0x113d7, 0x113d8 },
    { 0x1144b, 0x1145b },
    { 0x1145d, 0x1145d },
    { 0x114c6, 0x114c6 },
    { 0x114d0, 0x114d9 },
    { 0x115c1, 0x115d7 },
    { 0x11641, 0x11643 },
    { 0x11650, 0x11659 },
    { 0x11660, 0x1166c },
    { 0x116b9, 0x116b9 },
    { 0x116c0, 0x116c9 },
    { 0x116d0, 0x116e3 },
    { 0x11730, 0x1173e },
    { 0x1183b, 0x1183b },
    { 0x118e0, 0x118f2 },
    { 0x11944, 0x11946 },
    { 0x11950, 0x11959 },
    { 0x119e2, 0x119e2 },
    { 0x11a3f, 0x11a46 },
    { 0x11a9a, 0x11a9c },
    { 0x11a9e, 0x11aa2 },
    { 0x11b00, 0x11b09 },
    { 0x11be1, 0x11be1 },
    { 0x11bf0, 0x11bf9 },
    { 0x11c41, 0x11c45 },
    { 0x11c50, 0x11c6c },
    { 0x11c70, 0x11c71 },
    { 0x11d50, 0x11d59 },
    { 0x11da0, 0x11da9 },
    { 0x11ef7, 0x11ef8 },
    { 0x11f43, 0x11f59 },
    { 0x11fc0, 0x11fd4 },
    { 0x11fff, 0x11fff },
    { 0x12400, 0x1246e },
    { 0x12470, 0x12474 },
    { 0x12ff1, 0x12ff2 },
    { 0x16130, 0x16139 },
    { 0x16a60, 0x16a69 },
    { 0x16a6e, 0x16a6f },
    { 0x16ac0, 0x16ac9 },
    { 0x16af5, 0x16af5 },
    { 0x16b37, 0x16b3b },
    { 0x16b44, 0x16b44 },
    { 0x16b50, 0x16b59 },
    { 0x16b5b, 0x16b61 },
    { 0x16d6d, 0x16d79 },
    { 0x16e80, 0x16e9a },
    { 0x16fe2, 0x16fe2 },
    { 0x1bc9f, 0x1bc9f },
    { 0x1ccf0, 0x1ccf9 },
    { 0x1d2c0, 0x1d2d3 },
    { 0x1d2e0, 0x1d2f3 },
    { 0x1d360, 0x1d378 },
    { 0x1d7ce, 0x1d7ff },
    { 0x1da87, 0x1da8b },
    { 0x1e140, 0x1e149 },
    { 0x1e2f0, 0x1e2f9 },
    { 0x1e4f0, 0x1e4f9 },
    { 0x1e5f1, 0x1e5fa },
    { 0x1e5ff, 0x1e5ff },
    { 0x1e8c7, 0x1e8cf },
    { 0x1e950, 0x1e959 },
    { 0x1e95e, 0x1e95f },
    { 0x1ec71, 0x1ecab },
    { 0x1ecad, 0x1ecaf },
    { 0x1ecb1, 0x1ecb4 },
    { 0x1ed01, 0x1ed2d },
    { 0x1ed2f, 0x1ed3d },
    { 0x1f100, 0x1f10c },
    { 0x1fbf0, 0x1fbf9 },
};

/* Initial of the active alphabetic key; UTF-8 is decoded without losing non-ASCII names. */
static unsigned initial(int i) {
    void *r = deque_at(cf.albums, (unsigned)i);
    if (I(r, REC_ID) == -1) return '#';
    const char *name = cf.sort == SORT_ARTIST ? cover_artist(r) : P(r, REC_ALBUM);
    char key[ARTIST_KEY];
    snprintf(key, sizeof key, "%s", name ? name : "");
    ringnav_sort_key(key);
    const unsigned char *p = (const unsigned char *)key;
    unsigned c = *p++;
    if (c >= 0xc2 && c <= 0xf4) {
        unsigned n = c < 0xe0 ? 1 : c < 0xf0 ? 2 : 3;
        c &= (1u << (6 - n)) - 1;
        for (unsigned j = 0; j < n; ++j) {
            if ((*p & 0xc0) != 0x80) return '#';
            c = (c << 6) | (*p++ & 63);
        }
    }
    if (c >= 'a' && c <= 'z') c -= 32;
    if (c < 128) return c >= 'A' && c <= 'Z' ? c : '#';
    for (unsigned i = 0; i < sizeof initial_misc / sizeof *initial_misc; ++i)
        if (c >= initial_misc[i][0] && c <= initial_misc[i][1]) return '#';
    return c;
}

/* Utility cards and wrap edges remain individual stops. Reverse lands at a group's start. */
int coverflow_jump(void *w, int from, int dir, unsigned *letter) {
    int n = cf.albums ? (int)deque_size(cf.albums) : 0;
    *letter = 0;
    if (w != cf.slide || cf.screen != COVERS || cf.sort > SORT_ARTIST || cf.scoped ||
        from < 0 || from >= n) return 1;
    unsigned c = initial(from);
    int to = from + dir;
    if (dir > 0) {
        while (to < n && initial(to) == c) ++to;
        if (to < n) *letter = initial(to);
    } else {
        while (to >= 0 && initial(to) == c) --to;
        if (to < 0) return from + 1; /* the last album, across the wrap */
        c = initial(to);
        while (to > 0 && initial(to - 1) == c) --to;
        *letter = c;
    }
    return dir > 0 ? to - from : from - to;
}

typedef struct {
    void *r;
    const char *artist; /* Artist: the artist's sort key, without its article */
    unsigned rank;
    int idx;
} order_t;

static const char *album_name(void *r) {
    const char *s = P(r, REC_ALBUM);
    return s ? s : "";
}
/* Rows sorted to look up by binary search, ties in their list's order. */
typedef struct {
    void *a;
    unsigned j;
} row_t;
static int name_cmp(void *a, void *b) { return strcasecmp(album_name(a), album_name(b)); }
static int tie(int d, const row_t *p, const row_t *q) {
    return d ? d : (p->j > q->j) - (p->j < q->j);
}
static int by_name(const void *x, const void *y) {
    const row_t *p = x, *q = y;
    return tie(name_cmp(p->a, q->a), p, q);
}
static int by_album(const void *x, const void *y) {
    const row_t *p = x, *q = y;
    return tie(album_cmp(p->a, q->a), p, q);
}
/* The first of v's n rows not before r by cmp. */
static unsigned lower(row_t *v, unsigned n, void *r, int (*cmp)(void *, void *)) {
    unsigned lo = 0;
    while (n) {
        unsigned h = n / 2;
        if (cmp(v[lo + h].a, r) < 0) lo += h + 1, n -= h + 1;
        else n = h;
    }
    return lo;
}
/* list's rows sorted by by, *m of them, Unknown (REC_ID -1) only if all; 0 out of memory. */
static row_t *album_rows(void *list, unsigned *m, int (*by)(const void *, const void *), int all) {
    row_t *v = calloc(deque_size(list) + 1, sizeof *v);
    *m = 0;
    for (unsigned i = 0; v && i < deque_size(list); ++i)
        if (all || I(deque_at(list, i), REC_ID) != -1) v[*m].a = deque_at(list, i), v[(*m)++].j = i;
    if (v) qsort(v, *m, sizeof *v, by);
    return v;
}
/* The list row of r's album among album_rows' v, or -1: the Unknown row never matches. */
static int album_in(row_t *v, unsigned m, void *r) {
    unsigned k = lower(v, m, r, album_cmp);
    return k < m && !album_cmp(v[k].a, r) ? (int)v[k].j : -1;
}
/* cf.stock's album_rows while sorted() ranks it. */
static row_t *stock_rows;
static unsigned stock_n;
static int album_index(void *r) { return album_in(stock_rows, stock_n, r); }
/* album_row on the first four columns, then the fifth, the album artist, on the row it added; the
 * stSongInfo copy (0x5b3b1c) duplicates +0x24 as it does the other strings. */
static int split_row(void *a, int n, char **v, char **c) {
    void *dir = P(tools_pdeq_directory, 0);
    unsigned k = deque_size(dir);
    album_row(a, n - 1, v, c);
    if (deque_size(dir) > k && v[4]) P(deque_at(dir, k), REC_ALBUM_ARTIST) = strdup(v[4]);
    return 0;
}
static int rank_query(void *sql) { return toolsQueryDbTable("/mnt/data/database.db", sql, split_row, 0); }

/* list's albums with music, in a new deque (list goes), using a music track for their art even when
 * tags are shared with an audiobook. The card's files stay untouched. Out of memory,
 * audiobooks stay. */
static void *music_albums(void *list) {
    int n;
    void *rows = staged(rank_query, MUSIC_ALBUM_SQL, &n), *out;
    unsigned m;
    row_t *v = album_rows(rows, &m, by_album, 0);
    if (!v) return deque_destroy(rows), list;
    out = _create_deque("stSongInfo");
    deque_init(out);
    for (unsigned i = 0; i < deque_size(list); ++i) {
        void *r = deque_at(list, i);
        int k = I(r, REC_ID) == -1 ? -1 : album_in(v, m, r);
        if (k >= 0) _deque_push_back(out, deque_at(rows, (unsigned)k));
        else if (I(r, REC_ID) == -1 && m < deque_size(rows)) _deque_push_back(out, r);
    }
    free(v);
    deque_destroy(rows);
    deque_destroy(list);
    return out;
}

/* getAllAlbum groups by name alone, as stock's Albums list does: each of its rows becomes that
 * name's ALBUM_SQL rows, one per album_cmp album (the SQL keeps disc folders apart), in its place;
 * a row the query missed stays. Out of memory, the rows stay unsplit. ponytail: a scan of the
 * name's albums so far per row, its albums squared; fine unless one name has thousands. */
static void *split(void *stock) {
    int n;
    void *rows = staged(rank_query, ALBUM_SQL, &n), *out;
    unsigned m;
    row_t *v = album_rows(rows, &m, by_name, 1);
    if (!v) return deque_destroy(rows), stock;
    out = _create_deque("stSongInfo");
    deque_init(out);
    for (unsigned i = 0; i < deque_size(stock); ++i) {
        void *r = deque_at(stock, i);
        unsigned from = deque_size(out), k;
        for (unsigned j = I(r, REC_ID) == -1 ? m : lower(v, m, r, name_cmp); j < m && !name_cmp(v[j].a, r); ++j) {
            void *a = v[j].a;
            for (k = from; k < deque_size(out) && album_cmp(deque_at(out, k), a); ++k) {}
            if (k == deque_size(out)) _deque_push_back(out, a);
        }
        if (from == deque_size(out)) _deque_push_back(out, r);
    }
    free(v);
    deque_destroy(rows);
    deque_destroy(stock);
    return out;
}
/* navigation.c's Albums list (load_localclass_list 0xf003), split in place as Coverflow's: its size. */
int coverflow_split(void *list) {
    void *copy = _create_deque("stSongInfo"), *out;
    deque_init_copy(copy, list);
    out = split(copy);
    deque_clear(list);
    deque_assign(list, out);
    deque_destroy(out);
    return (int)deque_size(list);
}
/* list down to r's album (album_cmp), in order and in place, unless none is; then at's index.
 * navigation.c's album pages and playback.c's stock resume. */
int album_only(void *list, void *r, int at) {
    void *out = _create_deque("stSongInfo");
    int k = at;
    deque_init(out);
    for (unsigned i = 0; i < deque_size(list); ++i)
        if (!album_cmp(deque_at(list, i), r)) {
            if ((int)i == at) k = (int)deque_size(out);
            _deque_push_back(out, deque_at(list, i));
        }
    if (deque_size(out)) deque_clear(list), deque_assign(list, out);
    deque_destroy(out);
    return k;
}
static void rank_by(order_t *v, const char *sql) {
    int n;
    void *rows = staged(rank_query, (void *)sql, &n);
    for (unsigned j = 0; j < deque_size(rows); ++j) {
        int i = album_index(deque_at(rows, j));
        if (i >= 0 && v[i].rank == ~0u) v[i].rank = j;
    }
    deque_destroy(rows);
}
static int by_rank(const void *a, const void *b) {
    const order_t *x = a, *y = b;
    int unknown = (I(x->r, REC_ID) == -1) - (I(y->r, REC_ID) == -1); /* the Unknown card last */
    if (unknown) return unknown;
    return x->rank != y->rank ? (x->rank < y->rank ? -1 : 1) : x->idx - y->idx;
}
static int by_artist(const void *a, const void *b) {
    const order_t *x = a, *y = b;
    int d = (I(x->r, REC_ID) == -1) - (I(y->r, REC_ID) == -1);
    if (!d) d = strcasecmp(x->artist, y->artist);
    return d ? d : by_rank(a, b);
}

/* cf.stock in the Sort's order: cf.stock itself for Album. Out of memory, the Sort falls back to
 * Album too, so its card says what is shown. */
static void *sorted(void) {
    unsigned n = deque_size(cf.stock);
    int artist = cf.sort == SORT_ARTIST, played = cf.sort == SORT_PLAYED;
    order_t *v = cf.sort == SORT_ALBUM ? 0 : calloc(n + 1, sizeof *v);
    if (!cf.scoped) /* the rated page's sort is its own */
        cf.year_old = ringnav_year_old(), cf.artists = I(artist_type, 0);
    unsigned *sum = v && played ? calloc(n + 1, sizeof *sum) : 0;
    char *keys = v && artist ? calloc(n + 1, ARTIST_KEY) : 0;
    int ranked = cf.sort != SORT_RATING && cf.sort != SORT_RANDOM; /* uses album_index */
    stock_rows = v && ranked ? album_rows(cf.stock, &stock_n, by_album, 0) : 0;
    if (!v || (ranked && !stock_rows) || (played && !sum) || (artist && !keys)) {
        free(v), free(sum), free(keys), free(stock_rows), stock_rows = 0;
        cf.sort = SORT_ALBUM;
        return cf.stock;
    }
    for (unsigned i = 0; i < n; ++i) {
        void *r = v[i].r = deque_at(cf.stock, i);
        v[i].rank = ~0u, v[i].idx = (int)i;
        if (keys) {
            const char *a = cover_artist(r);
            snprintf(keys + i * ARTIST_KEY, ARTIST_KEY, "%s", a ? a : "");
            ringnav_sort_key(keys + i * ARTIST_KEY);
            v[i].artist = keys + i * ARTIST_KEY;
        }
    }
    if (cf.sort == SORT_RATING)
        for (unsigned i = 0; i < n; ++i) v[i].rank = 5u - (unsigned)cf.hearts(v[i].r);
    else if (sum) {
        album_plays(album_index, sum);
        for (unsigned i = 0; i < n; ++i)
            if (sum[i]) v[i].rank = ~sum[i]; /* most first; never played after, in stock order */
    } else if (cf.sort == SORT_RANDOM)
        for (unsigned i = 0; i < n; ++i) v[i].rank = rand_below(~0u);
    else
        rank_by(v, cf.sort == SORT_ADDED ? SORT_SQL "max(time_create) desc" : year_sql());
    qsort(v, n, sizeof *v, artist ? by_artist : by_rank);
    void *out = _create_deque("stSongInfo");
    deque_init(out);
    for (unsigned i = 0; i < n; ++i) _deque_push_back(out, v[i].r);
    free(v), free(sum), free(keys), free(stock_rows), stock_rows = 0;
    return out;
}
extern int ringnav_artist_az(void);
/* albums (split's rows) in place by year, as year_sql, or by name (Playback's Artist album order
 * A-Z): the query's order, then the albums it missed, then the Unknown album. navigation.c's artist pages. Out of memory they keep their order. */
void coverflow_by_year(void *albums) {
    unsigned n = deque_size(albums), m;
    char *taken = calloc(n + 1, 1);
    row_t *v = taken ? album_rows(albums, &m, by_album, 0) : 0;
    int found;
    if (!v) {
        free(taken);
        return;
    }
    void *rows = staged(rank_query, (void *)(ringnav_artist_az() ? SORT_SQL "album COLLATE NOCASE" : year_sql()), &found), *out = _create_deque("stSongInfo");
    deque_init(out);
    for (unsigned j = 0; j < deque_size(rows); ++j) {
        int i = album_in(v, m, deque_at(rows, j));
        if (i >= 0 && !taken[i]) taken[i] = 1, _deque_push_back(out, deque_at(albums, (unsigned)i));
    }
    deque_destroy(rows);
    for (int unknown = 0; unknown < 2; ++unknown)
        for (unsigned i = 0; i < n; ++i)
            if (!taken[i] && (I(deque_at(albums, i), REC_ID) == -1) == unknown)
                _deque_push_back(out, deque_at(albums, i));
    free(taken), free(v);
    deque_clear(albums);
    deque_assign(albums, out);
    deque_destroy(out);
}

void *text(void *parent, int x, int y, int w, int h) { /* shared with photos.c */
    void *label = hscroll_label_create(parent, x, y, w, h);
    widget_use_style(label, "s_scrlabel_white20c");
    set_hscroll_label_attribute(label);
    widget_set_prop_int(label, "loop", 1);
    return label;
}

/* A black page named name, with its destroy (given the page) and Return handlers; 0 when none. Shared with photos.c
 * and books.c. */
void *page_open(const char *name, int (*closed)(void *, void *), int (*keyup)(void *, void *)) {
    void *page = window_create(0, 0, 0, 0, 0);
    if (!page) return 0;
    widget_set_name(page, name);
    widget_set_prop_int(page, "style:normal:bg_color", (int)0xff000000u);
    widget_on(page, EVT_DESTROY, closed, page);
    widget_on(page, EVT_KEY_UP, keyup, 0);
    return page;
}

/* A page's 48px title bar; shared with photos.c. */
void *page_title(void *body, const char *caption) {
    void *title = text(body, CF_X, 0, CF_W, 48);
    widget_set_text_utf8(title, caption);
    return title;
}

/* The peq_ui.c page: a title bar (*title, unless 0) over n item_h rows in body, down to the page's
 * bottom (iPod: the last row's selection meets the screen's edge; Legacy: whole rows) or shrunk so
 * the list's white background never shows below a short list. Shared with photos.c. */
void *page_list(void *page, void *body, void **title, const char *caption, int n, int item_h) {
    int h = widget_get_prop_int(page, "h", 290), rows = IPOD ? h - 48 : (h - 48) / item_h * item_h;
    if (n * item_h < rows) rows = n * item_h;
    widget_destroy_children(body);
    widget_set_visible(body, 1, 0);
    void *t = page_title(body, caption);
    if (title) *title = t;
    void *lv = list_view_create(body, 0, 48, 375, rows);
    widget_set_prop_int(lv, "item_height", item_h);
    /* The theme's default list_view is a light card; stock pages paint theirs black inline. */
    widget_set_prop_int(lv, "style:normal:bg_color", (int)0xff000000u);
    widget_set_prop_int(lv, "style:normal:border_color", 0);
    void *view = scroll_view_create(lv, 0, 0, 375, rows);
    widget_set_prop_int(view, "yslidable", 1);
    widget_set_prop_int(view, "xslidable", 0);
    widget_set_prop_int(view, "virtual_h", n * item_h);
    widget_invalidate_force(page, 0);
    return view;
}

static void *list(const char *title, int n) {
    return page_list(cf.page, cf.body, &cf.title, title, n, 48);
}

/* One 48px row of a page_list or, with a detail (navigation.c's Most Played), a 64px one: the caption
 * over the detail in 16px #AAAAAA, stock's s_scrlabel_gray24l grey. iPod left-aligns them, as every
 * stock list. Returns the row. Shared with photos.c. */
void *page_row_detail(void *view, int index, const char *caption, const char *detail,
                      int (*click)(void *, void *)) {
    int h = detail ? 64 : 48;
    void *item = list_item_create(view, 0, index * h, 375, h);
    widget_use_style(item, "s_listitem_black");
    void *label = text(item, CF_ROW_X, detail ? 4 : 0, CF_ROW_W, detail ? 32 : 48);
    if (IPOD) widget_use_style(label, "s_scrlabel_white20l");
    widget_set_text_utf8(label, caption);
    if (detail) {
        label = text(item, CF_ROW_X, 36, CF_ROW_W, 24);
        if (IPOD) widget_use_style(label, "s_scrlabel_white20l");
        widget_set_prop_int(label, "style:normal:text_color", (int)0xffaaaaaau);
        widget_set_prop_int(label, "style:normal:font_size", 16);
        widget_set_text_utf8(label, detail);
    }
    widget_on(item, EVT_CLICK, click, (void *)(long)index);
    return item;
}

/* Stock's list_into chevron at the right of a full-width row h high, as the Library's (its glyph
 * at x 322 to 332), the row's labels narrowed to end before it. Shared with peq_ui.c. */
void row_into(void *item, int h) {
    for (unsigned i = 0, n = widget_count_children(item); i < n; ++i) {
        void *label = widget_get_child(item, i);
        widget_move_resize(label, I(label, W_X), I(label, W_Y), 316 - I(label, W_X), I(label, W_H));
    }
    image_base_set_image(image_create(item, 302, 0, 50, h), "list_into");
}

/* A page_row_detail row that opens another page, with row_into's chevron. An empty detail centres
 * the caption in its 64px row, beside the chevron. */
void *page_row_detail_into(void *view, int index, const char *caption, const char *detail,
                           int (*click)(void *, void *)) {
    void *item = page_row_detail(view, index, caption, detail, click);
    int h = detail ? 64 : 48;
    if (detail && !*detail) widget_move_resize(widget_get_child(item, 0), CF_ROW_X, 0, CF_ROW_W, h);
    row_into(item, h);
    return item;
}

void page_row_into(void *view, int index, const char *caption, int (*click)(void *, void *)) {
    page_row_detail_into(view, index, caption, 0, click);
}

/* clip: the canvas clip, read into old, narrowed to x, y, w, h; false when nothing shows. Shared
 * with navigation.c. */
int clip_within(void *canvas, int *old, int *clip, int x, int y, int w, int h) {
    canvas_get_clip_rect(canvas, old);
    clip[0] = old[0] > x ? old[0] : x;
    clip[1] = old[1] > y ? old[1] : y;
    clip[2] = (old[0] + old[2] < x + w ? old[0] + old[2] : x + w) - clip[0];
    clip[3] = (old[1] + old[3] < y + h ? old[1] + old[3] : y + h) - clip[1];
    if (clip[2] < 0) clip[2] = 0;
    if (clip[3] < 0) clip[3] = 0;
    return clip[2] && clip[3];
}

/* Resume, play counts, Books' pages, the last album and Boot to: each file is written whole, to a
 * .tmp, synced and renamed. Shared with navigation.c and books.c. */
int blob_io(const char *path, const char *tmp, void *buf, unsigned size, int write) {
    void *f = fopen(write ? tmp : path, write ? "wb" : "rb");
    if (!f) return 0;
    int ok = write ? fwrite(buf, size, 1, f) == 1 : fread(buf, size, 1, f) == 1;
    if (write && (fflush(f) || fsync(fileno(f)))) ok = 0;
    if (fclose(f) || !ok) {
        if (!write) memset(buf, 0, size);
        return 0;
    }
    return !write || !rename(tmp, path);
}

/* Stock pattern (album rows, Now Playing): load the file, set it, drop the load's reference, so the
 * next paint decodes the file again rather than a stale cached copy. Returns 0 when the load fails,
 * as for the empty "no art" marker. size, if given, gets the image's width and height (bitmap_t
 * w @0, h @4). Shared with spotify.c. */
int image_show(void *img, const char *url, unsigned *size) {
    unsigned bitmap[64]; /* bitmap_t */
    if (widget_load_image(img, url, bitmap)) return 0;
    if (size) size[0] = bitmap[0], size[1] = bitmap[1];
    image_base_set_image(img, url);
    widget_unload_image(img, bitmap);
    widget_invalidate_force(img, 0); /* A reused file URL still contains new pixels. */
    return 1;
}

/* Depth (docs/internals.md#coverflow-depth), after PictureFlow's renderer: every cover within reach
 * of the visual position is projected column by column into one frame bitmap, farthest first, with
 * its reflection, then the frame is drawn over the slide_menu, whose own children stay empty. All
 * integer: 16.16 turns and rotations, geometry in 1/16 px, row spans in 1/256 px. */
#define CF_ONE 65536
#define CF_REACH 3 /* ring slots either side of the centre that can show (fading in) or preload */
#define CF_RING (2 * CF_REACH + 1)
#define CF_TEXELS (ART_SIZE * ART_SIZE)
#define CF_EYE16 (16 * CF_EYE)
#define CF_HALF16 (16 * ART_SIZE / 2)
#define CF_MID256 (256 * (CF_TOP + ART_SIZE / 2)) /* the horizon every cover centres on */

/* cos and sin of 0 to CF_ANGLE (60) degrees in 16 steps, 16.16 */
static const int cf_cos[17] = { 65536, 65396, 64975, 64277, 63303, 62058, 60547, 58777, 56756,
                                54491, 51993, 49273, 46341, 43211, 39896, 36410, 32768 };
static const int cf_sin[17] = { 0,     4286,  8554,  12785, 16962, 21066, 25080, 28986, 32768,
                                36410, 39896, 43211, 46341, 49273, 51993, 54491, 56756 };

/* A right-hand cover t (16.16, >= 0) from the visual position: centre xc and depth zc (1/16 px),
 * rotation c, s (16.16) and brightness (of 256). Left covers mirror it. */
typedef struct {
    int xc, zc, c, s, bright;
} pose_t;

static void pose(int t, pose_t *p) {
    int a = t < CF_ONE ? t : CF_ONE, i = a >> 12, f = a & 4095;
    p->c = cf_cos[i] + (i < 16 ? (cf_cos[i + 1] - cf_cos[i]) * f >> 12 : 0);
    p->s = cf_sin[i] + (i < 16 ? (cf_sin[i + 1] - cf_sin[i]) * f >> 12 : 0);
    if (t < CF_ONE) {
        /* xc = A t + B t^2: CF_X1 at t = 1, arriving at the stack's CF_XS slope, and quick at
         * first, so the covers turning in and out mid-step keep clear of each other. */
        int sq = (int)((unsigned)t * (unsigned)t >> 16);
        p->xc = (16 * (2 * CF_X1 - CF_XS) * t + 16 * (CF_XS - CF_X1) * sq) >> 16;
        p->zc = 16 * CF_Z1 * t >> 16;
        p->bright = 256 - ((256 - CF_BRIGHT1) * t >> 16);
    } else {
        int e = t - CF_ONE;
        p->xc = 16 * CF_X1 + (16 * CF_XS * e >> 16);
        p->zc = 16 * CF_Z1 + (16 * CF_ZS * e >> 16);
        p->bright = e < CF_ONE ? CF_BRIGHT1 - ((CF_BRIGHT1 - CF_BRIGHT2) * e >> 16)
                    : e < 2 * CF_ONE ? CF_BRIGHT2 * (2 * CF_ONE - e) >> 16 : 0;
    }
}

/* Screen x (1/16 px right of the centre line) of the cover point u (1/16 px from its middle). */
static int project(const pose_t *p, int u) {
    return (p->xc + (u * p->c >> 16)) * CF_EYE16 / (CF_EYE16 + p->zc - (u * p->s >> 16));
}

/* The cover point under screen x dx (1/16 px right of the centre line), the inverse of project;
 * 0 when the column misses the cover. *h gets the cover's projected height there (1/256 px). */
static int unproject(const pose_t *p, int dx, int *u, int *h) {
    int den = (CF_EYE16 * p->c + dx * p->s) >> 16;
    if (den <= 0) return 0;
    *u = (dx * (CF_EYE16 + p->zc) - CF_EYE16 * p->xc) / den;
    if (*u < -CF_HALF16 || *u >= CF_HALF16) return 0;
    *h = ART_SIZE * 256 * CF_EYE16 / (CF_EYE16 + p->zc - (*u * p->s >> 16));
    return 1;
}

#define HOT static inline __attribute__((always_inline)) /* -Oz would call these per pixel */

/* p scaled by b (of 256), per 8-bit channel; alpha is left 0. */
HOT unsigned shade(unsigned p, unsigned b) {
    return ((p & 0xff00ffu) * b >> 8 & 0xff00ffu) | ((p & 0xff00u) * b >> 8 & 0xff00u);
}

/* The frame being drawn, nearest cover first: each pixel's alpha byte holds the coverage so far
 * (255 opaque), and a layer only fills what is left, which composites as drawing back to front
 * would. Per column, rows solid_top to solid_bottom are known opaque and skipped outright, so the
 * hidden parts of side covers cost nothing. */
typedef struct {
    unsigned *d;
    int pitch;
    short solid_top[CF_VIEW_W], solid_bottom[CF_VIEW_W];
} shelf_t;

/* colour (alpha ignored) under what is already at px, covering cov (of 256) of the pixel */
HOT void put(unsigned *px, unsigned colour, unsigned cov) {
    unsigned a = *px >> 24, have = a + (a >> 7), w = cov * (256 - have) >> 8;
    if (!a && cov == 256) {
        *px = colour | 0xff000000u;
        return;
    }
    if (!w) return;
    have += w;
    *px = ((*px & 0xffffffu) + shade(colour, w)) | (have > 255 ? 255u : have) << 24;
}

/* What dimming washes towards: black, or Light Mode's paper (set per frame). */
static unsigned cf_paper;

/* One texture column (texels ART_SIZE apart) h high (1/256 px) on the horizon at frame column col,
 * its partial end rows by coverage, then its reflection fading out below. */
static void column(shelf_t *f, int col, const unsigned *tex, int h, int bright, int inset) {
    if (h < 16) return;
    unsigned *d = f->d + col;
    int top = CF_MID256 - h / 2, bottom = top + h, step = (ART_SIZE << 20) / (h >> 4);
    int ins = h * inset / (ART_SIZE * 256), clipped_top = top + ins, clipped_bottom = bottom - ins;
    int solid_top = f->solid_top[col], solid_bottom = f->solid_bottom[col];
    int y = clipped_top >> 8, end = (clipped_bottom + 255) >> 8, last = end < CF_VIEW_H ? end : CF_VIEW_H;
    int first = y, final = end - 1; /* the rows the edges cross */
    int v = (((y << 8) + 128 - top) >> 4) * step >> 4, pitch = f->pitch; /* v: 16.16 texel row */
    if (y < 0) v -= y * step, y = 0;
    unsigned *px = d + y * pitch, wash = shade(cf_paper, 256u - (unsigned)bright);
    while (y < last) {
        if (y >= solid_top && y < solid_bottom) {
            int skip = solid_bottom - y;
            v += skip * step, px += skip * pitch, y = solid_bottom;
            continue;
        }
        int run = y < solid_top && solid_top < last ? solid_top : last; /* up to the opaque span */
        for (; y < run; ++y, v += step, px += pitch) {
            int row = v < 0 ? 0 : v >> 16 >= ART_SIZE ? ART_SIZE - 1 : v >> 16;
            unsigned cov = 256;
            if (y == first || y == final) {
                int from = y << 8 > clipped_top ? y << 8 : clipped_top,
                    to = (y + 1) << 8 < clipped_bottom ? (y + 1) << 8 : clipped_bottom;
                cov = (unsigned)(to - from);
            }
            put(px, shade(tex[row * ART_SIZE], (unsigned)bright) + wash, cov);
        }
    }
    /* The reflection starts where the body ends, sharing the row the body only partly covers. */
    int reflect = h * CF_REFLECT / ART_SIZE, stop = (bottom + reflect + 255) >> 8;
    int fade = (256 << 16) / reflect; /* fading per 1/256 px, 16.16 */
    unsigned dim = (unsigned)bright * CF_REFLECT_TOP >> 8;
    wash = shade(cf_paper, 256u - dim);
    if (stop > CF_VIEW_H) stop = CF_VIEW_H;
    for (y = (bottom + ins) >> 8, px = d + y * pitch; y < stop; ++y, px += pitch) {
        int from = y << 8 > bottom + ins ? y << 8 : bottom + ins,
            dist = ((from + ((y + 1) << 8)) >> 1) - bottom;
        if (dist >= reflect) break;
        if (y < 0 || (y >= solid_top && y < solid_bottom)) continue;
        int mirrored = dist * step >> 8 >> 16; /* texel rows up from the bottom edge */
        int row = ART_SIZE - 1 - (mirrored >= ART_SIZE ? ART_SIZE - 1 : mirrored);
        unsigned cov = (unsigned)(((y + 1) << 8) - from) * (256u - (unsigned)(dist * fade >> 16)) >> 8;
        put(px, shade(tex[row * ART_SIZE], dim) + wash, cov);
    }
    /* The rows this body covers whole are opaque now: join them to the known span, or keep the
     * longer of the two. */
    int s0 = (clipped_top + 255) >> 8, s1 = clipped_bottom >> 8;
    s0 = s0 < 0 ? 0 : s0, s1 = s1 > CF_VIEW_H ? CF_VIEW_H : s1;
    if (s1 <= s0) return;
    if (solid_bottom > solid_top && s0 <= solid_bottom && s1 >= solid_top) {
        s0 = s0 < solid_top ? s0 : solid_top;
        s1 = s1 > solid_bottom ? s1 : solid_bottom;
    } else if (s1 - s0 <= solid_bottom - solid_top)
        return;
    f->solid_top[col] = (short)s0, f->solid_bottom[col] = (short)s1;
}

/* The cover t (16.16, signed) turns from the visual position, under what is drawn. */
static unsigned short cf_inset[ART_SIZE] __attribute__((section(".scratch"))); /* 1/256 texel, symmetric; zero in Legacy */

static void draw_cover(shelf_t *f, int t, const unsigned *tex) {
    pose_t p;
    int side = t < 0 ? -1 : 1;
    pose(t * side, &p);
    if (p.bright <= 0 || !tex) return;
    int a = project(&p, -CF_HALF16), b = project(&p, CF_HALF16);
    int lo = (16 * CF_CX + (a < b ? a : b)) >> 4, hi = (16 * CF_CX + (a < b ? b : a)) >> 4;
    for (int v = lo; v <= hi; ++v) { /* v: the column as if the cover were on the right */
        int col = side > 0 ? v : 2 * CF_CX - 1 - v, u, h;
        if (col < 0 || col >= CF_VIEW_W || !unproject(&p, (2 * v + 1 - 2 * CF_CX) * 8, &u, &h)) continue;
        int k = (u + CF_HALF16) >> 4;
        column(f, col, tex + (side > 0 ? k : ART_SIZE - 1 - k), h, p.bright, cf_inset[k]);
    }
}

/* The ring slot of the k-th nearest pair's nearer (i 0) or farther (i 1) cover at frac: the side
 * the position is moving away from is farther. */
static int slot(int frac, int k, int i) {
    return (i == 0) == (frac < 0) ? CF_REACH - k : CF_REACH + k;
}

/* The shelf at frac (16.16, -CF_ONE/2 to under CF_ONE/2) past its centre cover: ring[j] is the
 * texture of the cover j - CF_REACH from the centre, or 0 to leave it out. Nearest first; nearer
 * covers and their reflections overlap farther ones. */
void coverflow_render(unsigned *d, int pitch, int frac, const unsigned *const ring[CF_RING]) {
#if IPOD
    if (!cf_inset[0]) { /* once, including callers which render without opening the page */
        int r = ART_SIZE * NP_ART_RADIUS / 166;
        for (int k = 0; k < r; ++k)
            cf_inset[k] = cf_inset[ART_SIZE - 1 - k] = (unsigned short)(arc_out(r, k) * 16);
    }
#endif
#if IPOD
    cf_paper = ipod_light() ? LIGHT_PAPER * 0x010101u : 0;
#endif
    shelf_t f;
    f.d = d, f.pitch = pitch;
    for (int x = 0; x < CF_VIEW_W; ++x) f.solid_top[x] = f.solid_bottom[x] = 0;
    for (int y = 0; y < CF_VIEW_H; ++y) memset(d + y * pitch, 0, CF_VIEW_W * 4);
    for (int k = 0; k <= CF_REACH; ++k)
        for (int i = 0; i < (k ? 2 : 1); ++i) {
            int j = slot(frac, k, i);
            draw_cover(&f, (j - CF_REACH) * CF_ONE - frac, ring[j]);
        }
    for (int y = 0; y < CF_VIEW_H; ++y) { /* over black; a count-down loop, which -Oz keeps tight */
        unsigned *p = d + y * pitch;
#if IPOD
        if (cf_paper) { /* over Light Mode's paper, as the page's black flips to it */
            for (int x = 0; x < CF_VIEW_W; ++x) put(p + x, cf_paper, 256);
            continue;
        }
#endif
        int x = CF_VIEW_W;
        do *p++ |= 0xff000000u;
        while (--x);
    }
}

#define CF_MISS 99

/* The ring offset of the frontmost cover body (not its reflection) drawn at frame pixel x, y at
 * frac, or CF_MISS; nearest first, in the drawing order, rounded corners (iPod) left out. A cover
 * fading out at the edge, under a quarter bright, is no target. */
int coverflow_hit(int frac, int x, int y) {
    for (int k = 0; k <= CF_REACH; ++k)
        for (int i = 0; i < (k ? 2 : 1); ++i) {
            int j = slot(frac, k, i), t = (j - CF_REACH) * CF_ONE - frac, side = t < 0 ? -1 : 1;
            int v = side > 0 ? x : 2 * CF_CX - 1 - x, u, h;
            pose_t p;
            pose(t * side, &p);
            if (p.bright < 64 || !unproject(&p, (2 * v + 1 - 2 * CF_CX) * 8, &u, &h)) continue;
            int ins = h * cf_inset[(u + CF_HALF16) >> 4] / (ART_SIZE * 256), top = CF_MID256 - h / 2;
            if ((y << 8) + 128 >= top + ins && (y << 8) + 128 < top + h - ins) /* column()'s corners */
                return j - CF_REACH;
        }
    return CF_MISS;
}

/* The depth renderer's state: the frame, the textures of the covers around the visual position
 * and the placeholder's. No frame: the flat fallback (stock images on the slide_menu). */
static struct {
    void *frame;                  /* bitmap_t *, CF_VIEW_W x CF_VIEW_H RGBA8888 */
    unsigned *tex;                /* CF_RING texture slots, then the placeholder */
    int album[CF_RING];           /* each slot's album + 1; 0 is empty */
    const unsigned *art[CF_RING]; /* its texture: the slot's own, or the placeholder */
    int c, frac, drawn, light;    /* the position and Light Mode the frame holds, once drawn */
} fx __attribute__((section(".scratch")));

/* A decoded image copied into a texture: fitted whole and centred, nearest sampled to ART_SIZE,
 * over black, so a non-square cover shows uncropped between black bars, as Now Playing shows it.
 * Only 32-bit formats (BITMAP_RGBA_AT); stock decodes covers to RGBA8888 with straight alpha,
 * composited over the page (black or Light Mode's paper). The image manager's copy is only read,
 * and a cover's load is dropped again at once, as stock does. */
static int decode(const char *url, unsigned *out, int unload) {
    static const unsigned char at[4][4] = BITMAP_RGBA_AT;
    unsigned bitmap[64]; /* bitmap_t */
    if (widget_load_image(cf.page, url, bitmap)) return 0;
    unsigned w = bitmap[0], h = bitmap[1], format = ((unsigned short *)bitmap)[7] - 1u;
    const unsigned char *data =
        format < 4 && w && h && w <= 4096 && h <= 4096 ? bitmap_lock_buffer_for_read(bitmap) : 0;
    if (data) {
        const unsigned char *o = at[format];
        unsigned stride = bitmap_get_line_length(bitmap), side = w > h ? w : h, xoff[ART_SIZE];
#if IPOD
        unsigned paper = ipod_light() ? LIGHT_PAPER : 0;
#else
        unsigned paper = 0;
#endif
        for (unsigned x = 0; x < ART_SIZE; ++x) { /* ~0u: a bar; left of the image wraps to one */
            unsigned sx = x * side / ART_SIZE - (side - w) / 2;
            xoff[x] = sx < w ? sx * 4 : ~0u;
        }
        for (unsigned y = 0; y < ART_SIZE; ++y) {
            unsigned sy = y * side / ART_SIZE - (side - h) / 2; /* wraps above the image */
            for (unsigned x = 0; x < ART_SIZE; ++x) {
                if (sy >= h || xoff[x] == ~0u) {
                    *out++ = 0xff000000u;
                    continue;
                }
                const unsigned char *px = data + sy * stride + xoff[x];
                unsigned a = px[o[3]], r = px[o[0]], g = px[o[1]], b = px[o[2]], k = 255 - a;
                /* over the page: black, or Light Mode's paper (the placeholder's corners) */
                if (a < 255)
                    r = (r * a + paper * k) / 255, g = (g * a + paper * k) / 255,
                    b = (b * a + paper * k) / 255;
                *out++ = 0xff000000u | r | g << 8 | b << 16;
            }
        }
        bitmap_unlock_buffer(bitmap);
    }
    if (unload) widget_unload_image(cf.page, bitmap);
    return data != 0;
}

static void fx_close(void) {
    if (fx.frame) bitmap_destroy(fx.frame);
    free(fx.tex);
    memset(&fx, 0, sizeof(fx));
}

static void fx_placeholder(void) {
    unsigned *placeholder = fx.tex + CF_RING * CF_TEXELS;
    if (!decode(PLACEHOLDER, placeholder, 0)) /* a theme image: the manager keeps it */
        for (int i = 0; i < CF_TEXELS; ++i) placeholder[i] = 0xff3a3a3au;
#if IPOD
    fx.light = ipod_light();
#endif
}
/* A w x h RGBA8888 bitmap the renderers fill whole, so flagged opaque; 0 when out of memory. */
static void *opaque_frame(int w, int h) {
    void *b = bitmap_create_ex(w, h, w * 4, 1 /* RGBA8888 */);
    if (b) *(unsigned short *)((char *)b + 0xc) |= 1; /* BITMAP_FLAG_OPAQUE */
    return b;
}

/* The frame and every texture at once, or neither: the flat covers then stand in. */
static int fx_open(void) {
    fx.frame = opaque_frame(CF_VIEW_W, CF_VIEW_H);
    fx.tex = fx.frame ? calloc(CF_RING + 1, CF_TEXELS * 4) : 0;
    if (!fx.tex) {
        fx_close();
        return 0;
    }
    fx_placeholder();
    return 1;
}

/* Album a's cached art in buf, else the placeholder (no art, or a card). */
static const unsigned *fx_load(int a, unsigned *buf) {
    char url[600] = "file://";
    if ((unsigned)a < deque_size(cf.albums)) {
        art_path(url + 7, album_key(deque_at(cf.albums, (unsigned)a)), "");
        if (art_exists(url) && decode(url, buf, 1)) return buf;
    }
    return fx.tex + CF_RING * CF_TEXELS;
}

/* The CF_RING covers around album c of n stay decoded, others are dropped as movement crosses
 * albums; ring gets each ring slot's texture. A small library repeats albums round the ring, as
 * the slide_menu wraps. */
static void fx_window(int c, int n, const unsigned *ring[CF_RING]) {
    int want[CF_RING];
    for (int j = 0; j < CF_RING; ++j) want[j] = ((c + j - CF_REACH) % n + n) % n + 1;
    for (int i = 0; i < CF_RING; ++i) {
        int keep = 0;
        for (int j = 0; j < CF_RING; ++j) keep |= fx.album[i] == want[j];
        if (!keep) fx.album[i] = 0;
    }
    for (int j = 0; j < CF_RING; ++j) {
        int i = 0;
        while (i < CF_RING && fx.album[i] != want[j]) ++i;
        if (i == CF_RING) { /* at most CF_RING albums are wanted, so a slot is free */
            for (i = 0; fx.album[i]; ++i) {}
            fx.album[i] = want[j];
            fx.art[i] = fx_load(want[j] - 1, fx.tex + i * CF_TEXELS);
            fx.drawn = 0;
        }
        ring[j] = fx.art[i];
    }
}

static int floor_div(int a, int b) { /* floor division, b > 0 */
    return a / b - (a % b < 0);
}

/* The visual position from the slide_menu's index and live offset (the wheel's animator): album c
 * (of n) at the centre and frac past it. Stock completion commits index - offset / stride. */
int visual(void *s, int n, int *c, int *frac) { /* shared with photos.c */
    int stride = slide_menu_item_width(s) + I(s, SLIDE_SPACER), d = -I(s, SLIDE_OFFSET);
    if (n <= 0 || stride <= 0) return 0;
    int q = floor_div(2 * d + stride, 2 * stride);
    *frac = (d - q * stride) * CF_ONE / stride;
    *c = ((I(s, SLIDE_INDEX) + q) % n + n) % n;
    return stride;
}

/* An artist's albums stop at both ends (home_step bounces there): a ring of one or two albums
 * would otherwise repeat round the shelf. */
int coverflow_bounded(void *w) { return w && w == cf.slide && cf.scoped == ARTIST_ALBUMS; }

static int retitle(const void *unused);

/* ringnav_paint (the border hook, after stock painted the slide_menu's empty children) calls this
 * for every widget: over Coverflow's slide_menu it draws the frame, rendered again only when the
 * position or a texture has changed. */
void coverflow_paint(void *w, void *canvas) {
    int c, frac, n;
    if (!w || w != cf.slide || !fx.frame || cf.screen != COVERS ||
        !visual(w, n = (int)widget_count_children(w), &c, &frac))
        return;
    const unsigned *ring[CF_RING];
#if IPOD
    if (fx.light != ipod_light()) {
        fx_placeholder();
        memset(fx.album, 0, sizeof fx.album); /* decoded art may have transparent corners too */
        fx.drawn = 0;
    }
#endif
    fx_window(c, n, ring);
    if (coverflow_bounded(w)) /* no wrap: only the artist's real neighbours, never repeats */
        for (int j = 0; j < CF_RING; ++j)
            if ((unsigned)(c + j - CF_REACH) >= (unsigned)n) ring[j] = 0;
    if (!fx.drawn || c != fx.c || frac != fx.frac) {
        unsigned *d = (unsigned *)bitmap_lock_buffer_for_write(fx.frame);
        if (!d) return;
        coverflow_render(d, (int)(bitmap_get_line_length(fx.frame) / 4), frac, ring);
        bitmap_unlock_buffer(fx.frame);
        fx.c = c, fx.frac = frac, fx.drawn = 1;
    }
    if (c + 1 != cf.titled && !cf.title_timer) cf.title_timer = timer_add(retitle, 0, 0);
    int r[4] = { 0, 0, CF_VIEW_W, CF_VIEW_H };
    canvas_draw_image(canvas, fx.frame, r, r);
}

/* Touch (docs/internals.md#coverflow-touch). The slide_menu stays insensitive: the covers' view
 * takes the touch and moves the offset itself, so stock's drag and throw never run. A drag follows
 * the finger, CF_DRAG_PX an album; the release glides on at the finger's speed, decelerating,
 * through the wheel's own animator (carousel_glide), to the cover that speed reaches; a tap brings
 * a side cover to the centre or opens the centre one. A touch while the covers move stops them.
 * The press is the covers' view's; the moves and the release are the page's, so a release off the
 * covers still ends the touch, and one never seen ends at the next key (coverflow_lost_touch). */
extern int carousel_glide(void *w, int goal, unsigned ms, int easing);
#define CF_SAMPLES 8
#define ABS(x) ((x) < 0 ? -(x) : (x))
static struct {
    int down, drag, caught, x, y, from, n; /* from: the offset at the press, or the drag's start */
    int sx[CF_SAMPLES];
    unsigned st[CF_SAMPLES];
} tc __attribute__((section(".scratch")));

static int stride_of(void *s) { return slide_menu_item_width(s) + I(s, SLIDE_SPACER); }
static int round_stride(int off, int stride) { return floor_div(2 * off + stride, 2 * stride) * stride; }

/* An artist's albums give past an end as iOS's rubber band, x * 0.55 / (1 + x * 0.55 / D) with
 * D 2/5 of a stride, so the centre never reaches past half a cover (visual() would wrap there);
 * not rubber: they stop at the end. */
static int bound(int off, int stride, int rubber) {
    void *s = cf.slide;
    if (!coverflow_bounded(s)) return off;
    int i = I(s, SLIDE_INDEX), hi = i * stride, lo = (i + 1 - (int)widget_count_children(s)) * stride;
    int edge = off > hi ? hi : off < lo ? lo : off, x = off - edge, a = ABS(x), d = 2 * stride / 5;
    if (!x || !rubber) return edge;
    a = a * 11 * d / (a * 11 + 20 * d);
    return edge + (x < 0 ? -a : a);
}

/* bound's inverse past an end: a touch catching the rubber band (or a wheel bounce) drags on from
 * where it shows, not from a band stretched again. */
static int unbound(int off, int stride) {
    void *s = cf.slide;
    if (!coverflow_bounded(s)) return off;
    int i = I(s, SLIDE_INDEX), hi = i * stride, lo = (i + 1 - (int)widget_count_children(s)) * stride;
    int edge = off > hi ? hi : off < lo ? lo : off, x = off - edge, a = ABS(x), d = 2 * stride / 5;
    if (!x || a >= d) return off;
    a = 20 * d * a / (11 * (d - a));
    return edge + (x < 0 ? -a : a);
}

/* px/s over the last 100 ms of moves; 0 if the finger rested CF_STILL_MS before lifting. */
static int velocity(unsigned now) {
    int last = (tc.n - 1) % CF_SAMPLES, first = last;
    if (!tc.n || now - tc.st[last] > CF_STILL_MS) return 0;
    for (int k = 1; k < (tc.n < CF_SAMPLES ? tc.n : CF_SAMPLES); ++k) {
        int j = (tc.n - 1 - k) % CF_SAMPLES;
        if (tc.st[last] - tc.st[j] > 100) break;
        first = j;
    }
    int dt = (int)(tc.st[last] - tc.st[first]), v = dt ? (tc.sx[last] - tc.sx[first]) * 1000 / dt : 0;
    return v > CF_VMAX ? CF_VMAX : v < -CF_VMAX ? -CF_VMAX : v; /* a burst of queued moves */
}

static void sample(int x, unsigned t) {
    tc.sx[tc.n % CF_SAMPLES] = x, tc.st[tc.n % CF_SAMPLES] = t;
    ++tc.n;
}

/* When the event was posted: moves queued behind a frame are dispatched together, but keep their
 * own times. 0 (unstamped) takes the dispatch time. */
static unsigned stamp(void *event) {
    unsigned t = (unsigned)I(event, EVENT_TIME);
    return t ? t : time_now_ms();
}

/* The covers glide on from the release (v px/s, 0 to settle) to a whole cover: where the speed
 * carries them in CF_PROJECT_MS, never back against a flick, starting at the finger's speed. */
static void release(int v) {
    void *s = cf.slide;
    int stride = stride_of(s), live = I(s, SLIDE_OFFSET);
    if (stride <= 0) return;
    int vo = v * stride / CF_DRAG_PX, goal = round_stride(live + vo * CF_PROJECT_MS / 1000, stride);
    int flick = ABS(v) >= CF_FLICK;
    if (flick && v > 0 && goal < -floor_div(-live, stride) * stride) goal = -floor_div(-live, stride) * stride;
    if (flick && v < 0 && goal > floor_div(live, stride) * stride) goal = floor_div(live, stride) * stride;
    goal = bound(goal, stride, 0);
    if (goal == live && !live && !P(s, SLIDE_ANIMATOR)) return; /* at rest already */
    int d = ABS(goal - live), a = ABS(vo), ahead = (goal > live && v > 0) || (goal < live && v < 0);
    unsigned ms = ahead ? (unsigned)(3000 * d / a) : CF_SETTLE_MS; /* cubic out: 3 d / v */
    carousel_glide(s, goal, ms < CF_SETTLE_MS ? CF_SETTLE_MS : ms > CF_GLIDE_MAX ? CF_GLIDE_MAX : ms,
                   EASE_CUBIC_OUT);
}

/* A tap at rest: the cover drawn under it comes to the centre, or opens if it is there. */
static void tap(int x, int y) {
    void *s = cf.slide;
    int n = (int)widget_count_children(s), c, frac, xy[2] = { x, y };
    int stride = visual(s, n, &c, &frac);
    if (!stride) return;
    widget_to_local(s, xy);
    int j = fx.frame ? coverflow_hit(frac, xy[0], xy[1])
            : ABS(xy[0] - CF_CX) < ART_SIZE / 2 && xy[1] >= 0 && xy[1] < ART_SIZE
                ? 0
                : CF_MISS; /* the flat fallback: the centre cover only */
    if (j == CF_MISS || (coverflow_bounded(s) && (unsigned)(c + j) >= (unsigned)n)) return;
    if (!j && !frac)
        pick((void *)(long)c, 0);
    else
        carousel_glide(s, round_stride(I(s, SLIDE_OFFSET), stride) - j * stride, CF_TAP_MS, SLIDE_EASING);
}

static int covers_live(void) { return cf.slide && cf.screen == COVERS && !cf.timer; }

/* The touch ends at now: a drag glides on (thrown unless aborted), a stopped glide settles, a
 * still touch is a tap. Off the covers (a screen change mid-touch) they still settle on a whole
 * cover. */
static void lift(int aborted, unsigned now) {
    if (!tc.down) return;
    tc.down = 0;
    if (!cf.slide) return;
    if (tc.drag || tc.caught || !covers_live())
        release(tc.drag && !aborted && covers_live() ? velocity(now) : 0);
    else if (!aborted) tap(tc.x, tc.y);
}

static int touch(void *ctx, void *event) {
    int type = I(event, EVENT_TYPE), x = I(event, EVENT_X), y = I(event, EVENT_Y);
    if (type == EVT_POINTER_DOWN) {
        lift(1, 0); /* a release the input queue dropped */
        if (!covers_live()) return 0;
        void *a = P(cf.slide, SLIDE_ANIMATOR);
        if (a) widget_animator_pause(a); /* caught where it is */
        tc = (typeof(tc)){ .down = 1, .caught = a != 0, .x = x, .y = y,
                           .from = unbound(I(cf.slide, SLIDE_OFFSET), stride_of(cf.slide)) };
        return 0;
    }
    if (!tc.down) return 0;
    if (type == EVT_POINTER_UP_BEFORE || type == EVT_POINTER_ABORT) {
        lift(type == EVT_POINTER_ABORT, stamp(event));
        return 0;
    }
    if (type != EVT_POINTER_MOVE_BEFORE || !covers_live()) return 0;
    int dx = x - tc.x, dy = y - tc.y;
    if (!tc.drag) {
        if (ABS(dx) < CF_SLOP && ABS(dy) < CF_SLOP) return 0;
        if (ABS(dy) > ABS(dx)) { /* up or down: stock's (the Quick Settings pull-down) */
            lift(1, 0);
            return 0;
        }
        tc.drag = 1, tc.x = x, dx = 0; /* from here, so the covers don't jump the slop */
    }
    sample(x, stamp(event));
    int stride = stride_of(cf.slide);
    I(cf.slide, SLIDE_OFFSET) = bound(tc.from + dx * stride / CF_DRAG_PX, stride, 1);
    widget_invalidate_force(cf.slide, 0);
    return 0;
}

/* ringnav, at a key with no finger down: a touch whose release went unseen ends, so the wheel
 * and Centre act on the cover the covers settle on. */
void coverflow_lost_touch(void) { lift(1, 0); }


static void cover(void *img, unsigned i, int near) {
    char url[600] = "file://";
    near = near && i < deque_size(cf.albums);
    if (near) art_path(url + 7, album_key(deque_at(cf.albums, i)), "");
    if (tk_strcmp(widget_get_prop_str(img, "image", ""), near ? url : PLACEHOLDER) &&
        (!near || !art_exists(url) || !image_show(img, url, 0)))
        image_base_set_image(img, PLACEHOLDER);
}

/* The captions name album c (or a card's), once each time it changes. */
static void titles(unsigned c) {
    unsigned n = widget_count_children(cf.slide);
    if ((int)c + 1 == cf.titled) return;
    cf.titled = (int)c + 1;
    void *r = c + CARDS < n ? deque_at(cf.albums, c) : (void *)0;
    widget_set_text_utf8(cf.name, r                            ? P(r, REC_ALBUM)
                                  : cf.scoped == ARTIST_ALBUMS ? "All albums"
                                                               : sort_names[cf.sort]);
    widget_set_text_utf8(cf.artist, r ? cover_artist(r) : ""); /* NULL leaves the old one */
    /* Stock draws even a centred caption at -xoffset: a long one's scroll would shift the next
     * left. */
    widget_set_prop_int(cf.name, "xoffset", 0);
    widget_set_prop_int(cf.artist, "xoffset", 0);
}

static int changed(void *ctx, void *event) {
    unsigned n = widget_count_children(cf.slide), c = (unsigned)I(cf.slide, SLIDE_INDEX);
    for (unsigned i = 0; !fx.frame && i < n; ++i) { /* the flat fallback's own images */
        unsigned d = i > c ? i - c : c - i;
        cover(widget_get_child(cf.slide, i), i, d <= ART_NEAR || n - d <= ART_NEAR);
    }
    void *r = c + CARDS < n ? deque_at(cf.albums, c) : (void *)0;
    if (r && !cf.scoped) cf.saved_album = album_key(r);
    cf.titled = 0; /* a commit always writes them */
    titles(c);
    return 0;
}

/* The centre cover the frame last drew, named outside the paint. */
static int retitle(const void *unused) {
    cf.title_timer = 0;
    if (cf.slide && cf.screen == COVERS) titles((unsigned)fx.c);
    return 0;
}

/* One child per album plus the cards (CARDS), moved by the wheel and by touch on the covers' view
 * (touch); the slide_menu itself takes no touch. With depth it spans the frame, so every step repaints all of it, and its CF_VIEW_H square
 * items with a negative spacer move one album per CF_STRIDE px; the children stay empty under the
 * frame. The flat fallback is the stock images, 160 px, as before. ponytail: one child per album;
 * if large libraries lag on hardware, virtualize to a recycled window of children. */
static void covers(void) {
    cf.screen = COVERS;
    if (cf.text_albums) {
        unsigned n = deque_size(cf.albums);
        void *view = list("Albums", (int)n);
        for (unsigned i = 0; i < n; ++i)
            page_row_into(view, (int)i, P(deque_at(cf.albums, i), REC_ALBUM), pick);
        return;
    }
    widget_set_visible(cf.body, 0, 0);
    if (!cf.covers) {
        void *f = widget_factory();
        int depth = fx_open();
        cf.covers = widget_factory_create_widget(f, "view", cf.page, 0, 0, 375, 290);
        cf.slide = widget_factory_create_widget(f, "slide_menu", cf.covers, 0, depth ? 0 : 24, 375,
                                                depth ? CF_VIEW_H : ART_SIZE);
        if (depth) slide_menu_set_spacer(cf.slide, CF_STRIDE - CF_VIEW_H);
        widget_set_sensitive(cf.slide, 0);
        for (unsigned i = 0, n = deque_size(cf.albums); i < n + CARDS; ++i) {
            void *img = image_create(cf.slide, 0, 0, 0, 0);
            image_set_draw_type(img, 4); /* scale_auto, as the stock cover rows */
            if (!depth) image_base_set_image(img, PLACEHOLDER);
            widget_set_prop_int(img, "clickable", 1);
            widget_on(img, EVT_CLICK, pick, (void *)(long)i);
        }
        /* Album over artist under the frame, white and larger, then grey, clear of the rounded
         * glass (docs/ipod.md#coverflow); the same in both builds. */
        cf.name = text(cf.covers, CF_EDGE, CF_TEXT_Y, 375 - 2 * CF_EDGE, CF_NAME_H);
        widget_set_prop_int(cf.name, "style:normal:font_size", CF_NAME_PX);
        cf.artist = text(cf.covers, CF_EDGE, CF_TEXT_Y + CF_NAME_H, 375 - 2 * CF_EDGE, CF_ARTIST_H);
        widget_set_prop_int(cf.artist, "style:normal:font_size", CF_ARTIST_PX);
        widget_set_prop_int(cf.artist, "style:normal:text_color", (int)CF_GREY);
        slide_menu_set_value(cf.slide, cf.album);
        widget_on(cf.slide, EVT_VALUE_CHANGED, changed, 0);
        tc.down = 0; /* a touch on the covers these replace */
        changed(0, 0);
        widget_on(cf.covers, EVT_POINTER_DOWN, touch, 0); /* the rest: open_page */
    }
    widget_set_visible(cf.covers, 1, 0);
    widget_set_sensitive(cf.covers, 1);
    widget_invalidate_force(cf.page, 0);
}

static int to_covers(const void *unused) {
    cf.timer = 0;
    stop();
    covers();
    return 0;
}

static int cancel_row(void *ctx, void *event) {
    rearm(&cf.timer, to_covers, 0);
    return 0;
}

static int poll(const void *unused) {
    cf.timer = 0;
    if (cf.done == cf.total) return to_covers(0); /* stop() joins the thread's last steps */
    char progress[64];
    tk_snprintf(progress, sizeof(progress), "Preparing artwork\xe2\x80\xa6 %d/%d", cf.done,
                cf.total);
    widget_set_text_utf8(cf.title, progress);
    cf.timer = timer_add(poll, 0, 250);
    return 0;
}

/* On open: the albums, then art for the ones with no cache file (PictureFlow's first-launch build;
 * later opens resume). The albums are queried again only after the library changed (or Refresh
 * Coverflow): stock's sort converts both names to pinyin on every comparison. */
static void load(void) {
    int on_sort = cf.on_sort; /* taken whatever this load shows */
    cf.on_sort = 0;
    if (cf.resort && !cf.scoped) { /* Coverflow Sort changed: its order read again */
        cf.resort = cf.sort_read = 0;
        if (cf.albums && cf.albums != cf.stock) deque_destroy(cf.albums);
        cf.albums = 0;
    }
    drop();
    widget_destroy_children(cf.page);
    cf.covers = cf.slide = cf.name = cf.artist = 0; /* destroyed with the page's children */
    cf.album = 0;
    cf.body = widget_factory_create_widget(widget_factory(), "view", cf.page, 0, 0, 375, 290);
    int n = 0;
    unsigned gen = library_gen;
    if (cf.albums_gen != gen && !cf.scoped) drop_albums();
    if (cf.scoped)
        n = (int)deque_size(cf.scoped == RATED_ALBUMS ? cf.stock : cf.albums);
    else if (cf.stock)
        n = (int)deque_size(cf.stock);
    else if (!*(volatile int *)SCAN_THREAD || *(volatile int *)SCAN_DONE) {
        cf.stock = staged(albums, 0, &n);
        if (n > 0) cf.stock = music_albums(split(cf.stock));
        cf.albums_gen = gen;
        drop_unknown();
        n = (int)deque_size(cf.stock);
    }
    if (cf.albums && !cf.scoped && (cf.sort == SORT_YEAR || cf.sort == SORT_ARTIST) &&
        (cf.year_old != ringnav_year_old() || /* Year order changed, or Artists for its names */
         (cf.sort == SORT_ARTIST && cf.artists != I(artist_type, 0)))) {
        if (cf.albums != cf.stock) deque_destroy(cf.albums);
        cf.albums = 0;
    }
    if (n > 0 && !cf.albums) {
        if (!cf.sort_read) BLOB_IO(SORT_FILE, cf.sort, 0), cf.sort_read = 1;
        cf.sort = sort_next(0);
        cf.albums = sorted();
    }
    if (n <= 0) {
        drop_albums(); /* only a real list is kept */
        cf.screen = COVERS; /* Return goes Home */
        list("Update Local Music first", 0);
        return;
    }
    unsigned count = deque_size(cf.albums);
    if (cf.text_albums) {
        covers();
        return; /* Text lists need no cover cache or decoding. */
    }
    char path[512];
    if (!cf.saved_album) BLOB_IO(LAST_ALBUM, cf.saved_album, 0); /* outlives a reboot */
    cf.jobs = calloc(count, sizeof(job_t));
    for (unsigned i = 0; i < count; ++i) {
        void *r = deque_at(cf.albums, i);
        unsigned key = album_key(r);
        if (key == cf.saved_album && !on_sort && !cf.scoped) cf.album = (int)i;
        job_t *j = cf.jobs ? &cf.jobs[cf.total] : 0;
        if (j && P(r, REC_PATH) && access(art_path(path, key, ""), 0) && (j->track = strdup(P(r, REC_PATH)))) {
            j->key = key;
            j->shared = folder_shared(key, j->track);
            ++cf.total;
        }
    }
    if (on_sort) cf.album = (int)count; /* the Sort card, pressed again and again */
    mkdir(ART_DIR, 0755);
    cf.done = cf.cancel = 0;
    if (cf.total && card_space(ART_DIR) && !pthread_create(&cf.thread, 0, worker, 0)) {
        cf.running = 1;
        cf.screen = PREPARING;
        page_row_detail(list("", 1), 0, "Cancel", 0, cancel_row);
        poll(0);
    } else
        to_covers(0);
}

/* Album queues use stock's album class (0xff10), never folder class 1: Folder Skip only acts on
 * class 1 and would load the next directory's untagged files. Stock resume rebuilds 0xff10 with
 * getMusicByAlbum from the resume record's album, which player_load_songlist fills for stock lists. */
void album_memory(void *r) {
    const char *album = P(r, REC_ALBUM);
    unsigned char *m = (unsigned char *)g_memory_info;
    m[0x60d] = !album || !*album; /* Unknown album */
    tk_snprintf((char *)m + 0x611, 0x100, "%s", album ? album : "");
}

/* Start a queue at idx with its playback class; playing_page copies dq synchronously. */
void play_folder(void *dq, int idx, int cls) {
    if (cls == 0xff10) album_memory(deque_at(dq, (unsigned)idx));
    struct {
        void *dq;
        int idx, cls, mode;
    } context = { dq, idx, cls, 2 };
    navigator_to_with_context("playing_page", &context);
}

static int play(void *ctx, void *event) {
    int i = (int)(long)ctx;
    void *t = deque_at(cf.tracks, (unsigned)i);
    if (!t || access(P(t, REC_PATH), 0)) {
        widget_set_text_utf8(cf.title, "Storage unavailable");
        return 0;
    }
    play_folder(cf.tracks, i, 0xff10);
    return 0;
}

/* Album order: disc, then track, then path, so an untagged album keeps its file-name order and a
 * CUE image's tracks (one path) their start times. */
int album_before(const void *pa, const void *pb) {
    void *a = *(void *const *)pa, *b = *(void *const *)pb;
    int d = I(a, REC_DISC) - I(b, REC_DISC);
    if (!d) d = I(a, REC_TRACK) - I(b, REC_TRACK);
    if (!d) d = strcmp(P(a, REC_PATH), P(b, REC_PATH));
    return d ? d : I(a, REC_CUE_START) - I(b, REC_CUE_START);
}

/* album's tracks (getMusicByAlbum finds every album of its name) in album order, in a new deque;
 * all of them in stock's name order when out of memory. */
static void *in_order(void *tracks, void *album) {
    unsigned n = 0;
    void **v = calloc(deque_size(tracks) + 1, sizeof *v);
    if (!v) return tracks;
    for (unsigned i = 0; i < deque_size(tracks); ++i)
        if (I(album, REC_ID) == -1 || !album_cmp(deque_at(tracks, i), album)) v[n++] = deque_at(tracks, i);
    qsort(v, n, sizeof *v, album_before);
    void *out = _create_deque("stSongInfo");
    deque_init(out);
    for (unsigned i = 0; i < n; ++i) _deque_push_back(out, v[i]);
    free(v);
    deque_destroy(tracks);
    return out;
}

/* A track's tagged title, else its file name without the extension; CUE names stay whole.
 * Shared with navigation.c's Most Played and scrobbler. */
const char *track_name(char *buf, unsigned size, void *t) {
    const char *title = P(t, REC_TITLE);
    if (title && *title) return title;
    const char *name = P(t, REC_NAME), *path = P(t, REC_PATH), *ext = 0;
    if (!name || !path) return name;
    for (; *path; ++path)
        if (*path == '.')
            ext = path;
        else if (*path == '/')
            ext = 0;
    if (!ext) return name;
    unsigned n = strlen(name), e = strlen(ext);
    if (n <= e || strcmp(name + n - e, ext)) return name;
    snprintf(buf, size, "%.*s", (int)(n - e), name);
    return buf;
}

extern void *staged_music(int (*query)(void *), void *arg, int *count);
void *coverflow_album_tracks(void *r) {
    int n;
    return in_order(staged_music(albums, r, &n), r);
}

static int to_tracks(const void *unused) {
    cf.timer = 0;
    int n;
    char name[512];
    void *r = deque_at(cf.albums, (unsigned)cf.album);
    if (!cf.scoped) {
        cf.saved_album = album_key(r);
        BLOB_IO(LAST_ALBUM, cf.saved_album, 1); /* a power-off on the tracks keeps it too */
    }
    if (cf.tracks) deque_destroy(cf.tracks);
    cf.tracks = coverflow_album_tracks(r);
    n = (int)deque_size(cf.tracks);
    cf.screen = TRACKS;
    if (cf.covers) { /* none in the text list; stock's hit test skips insensitive, not hidden,
                      * widgets, and the covers' view over the list would take its taps and swipes */
        widget_set_visible(cf.covers, 0, 0);
        widget_set_sensitive(cf.covers, 0);
    }
    void *view = list(P(r, REC_ALBUM), n);
    /* the album over its tracks, all centred */
    widget_set_prop_int(cf.title, "style:normal:font_size", CF_NAME_PX);
    for (int i = 0; i < n; ++i) {
        void *row = page_row_detail(
            view, i, track_name(name, sizeof name, deque_at(cf.tracks, (unsigned)i)), 0, play);
        widget_use_style(widget_get_child(row, 0), "s_scrlabel_white20c");
    }
    return 0;
}

/* The All albums card: every album's tracks, the covers' order and each album's in album order,
 * from the first, as a library queue (0xff10 would resume one album of the name). ponytail: a
 * query per album; one artist query split back into albums if a large artist drags. */
static int play_all(const void *unused) {
    cf.timer = 0;
    void *all = _create_deque("stSongInfo");
    deque_init(all);
    for (unsigned i = 0; i < deque_size(cf.albums); ++i) {
        void *t = coverflow_album_tracks(deque_at(cf.albums, i));
        for (unsigned j = 0; j < deque_size(t); ++j) _deque_push_back(all, deque_at(t, j));
        deque_destroy(t);
    }
    if (deque_size(all) && !access(P(deque_at(all, 0), REC_PATH), 0))
        play_folder(all, 0, 0xf001);
    else
        widget_set_text_utf8(cf.name, "Storage unavailable");
    deque_destroy(all);
    return 0;
}

/* Display settings' Refresh Coverflow (navigation.c): the art cache emptied and the albums queried
 * again, both at Coverflow's next open (a page open under Settings keeps what it shows). The
 * library generation's step is a songtable write's, which Most Played reads again too. */
void coverflow_refresh_art(void) {
    void *dir = opendir(ART_DIR);
    for (struct dirent *e; dir && (e = readdir(dir));) {
        char path[600];
        snprintf(path, sizeof(path), ART_DIR "/%s", e->d_name);
        unlink(path); /* "." and ".." fail harmlessly */
    }
    if (dir) closedir(dir);
    ++library_gen;
}

/* The library's order as SORT_FILE holds it, Album when it holds none of all_sorts. */
static int sort_saved(void) {
    int sort = SORT_ALBUM;
    BLOB_IO(SORT_FILE, sort, 0);
    for (unsigned i = 0; i < sizeof all_sorts; ++i)
        if (all_sorts[i] == sort) return sort;
    return SORT_ALBUM;
}

/* Display settings' Coverflow Sort: its label ("Sort: Album"), and a press's next order, saved,
 * which Coverflow takes at its next open (load). */
const char *coverflow_sort_name(void) { return sort_names[sort_saved()]; }
void coverflow_sort_step(void) {
    int sort = sort_saved();
    unsigned i = 0;
    while (all_sorts[i] != sort) ++i;
    sort = all_sorts[(i + 1) % sizeof all_sorts];
    BLOB_IO(SORT_FILE, sort, 1);
    cf.resort = 1;
}

/* The rated albums' Sort card: the next order, saved, and the covers again in it, still on the Sort
 * card. The stock list is kept, so nothing is queried but the order's own ranking. */
static int resort(const void *unused) {
    cf.timer = 0;
    cf.sort = sort_next(1);
    if (cf.scoped) BLOB_IO(RATED_SORT_FILE, cf.sort, 1);
    else BLOB_IO(SORT_FILE, cf.sort, 1);
    if (cf.albums != cf.stock) deque_destroy(cf.albums);
    cf.albums = 0;
    cf.on_sort = 1;
    load();
    return 0;
}

/* Clicks only schedule: a screen change never destroys the widget whose click is running. */
static int pick(void *ctx, void *event) {
    int i = (int)(long)ctx, n = (int)deque_size(cf.albums);
    if (i == n && cf.scoped == ARTIST_ALBUMS)
        rearm(&cf.timer, play_all, 0);
    else if (i == n)
        rearm(&cf.timer, resort, 0);
    else {
        cf.album = i;
        rearm(&cf.timer, to_tracks, 0);
    }
    return 0;
}

/* The artist page right under an artist's covers, which they close with them (keyup,
 * coverflow_artist), or 0. */
static void *artist_under(void) {
    void *wm = window_manager(), *under = 0;
    for (unsigned i = 1, n = cf.scoped == ARTIST_ALBUMS ? widget_count_children(wm) : 0; i < n; ++i)
        if (widget_get_child(wm, i) == cf.page) under = widget_get_child(wm, i - 1);
    if (under && tk_strcmp(widget_get_prop_str(under, "name", ""), "artistinfo_page")) under = 0;
    return under;
}

/* Return: tracks -> covers (the slide_menu kept its album), preparing -> cancel and covers,
 * covers or the message -> Home, or back out of a scoped page (one album's tracks too). */
static int keyup(void *ctx, void *event) {
    if (I(event, EVENT_KEY) != KEY_RETURN) return 0;
    if ((cf.screen == TRACKS && cf.scoped != ALBUM_TRACKS) || cf.screen == PREPARING)
        rearm(&cf.timer, to_covers, 0);
    else if (cf.scoped) {
        /* An artist's covers: the unseen artist page under them closes first, unanimated (its
         * own Return is navigator_back too; its cleanup runs on EVT_WINDOW_CLOSE), so Return
         * lands on the Artists list. */
        void *under = artist_under();
        if (under) window_manager_close_window_force(window_manager(), under);
        navigator_back();
    } else
        navigator_back_to_home();
    return 11; /* RET_STOP */
}

static int closed(void *ctx, void *event) {
    if (ctx != cf.page) return 0; /* a page scoped_open closed already */
    stop_timer(&cf.title_timer);
    tc.down = 0;
    if (cf.saved_album) BLOB_IO(LAST_ALBUM, cf.saved_album, 1);
    drop();
    cf.page = cf.body = cf.covers = cf.slide = 0;
    if (cf.scoped == RATED_ALBUMS) {
        if (cf.albums != cf.stock) deque_destroy(cf.albums);
        deque_destroy(cf.stock);
        cf.stock = cf.all_stock;
        cf.sort = cf.all_sort;
    } else if (cf.scoped)
        deque_destroy(cf.albums);
    if (cf.scoped) cf.albums = cf.all_albums;
    cf.scoped = 0;
    cf.text_albums = 0;
    return 0;
}

/* A Coverflow page: the covers' moves and release are taken here (touch). */
static void *open_page(void) {
    void *page = page_open("coverflow_page", closed, keyup);
    static const unsigned short kinds[] = { EVT_POINTER_MOVE_BEFORE, EVT_POINTER_UP_BEFORE, EVT_POINTER_ABORT };
    for (unsigned k = 0; page && k < sizeof kinds / sizeof *kinds; ++k) widget_on(page, kinds[k], touch, 0);
    return page;
}

static int coverflow_open(void *ctx, void *event) {
    if (cf.page || !(cf.page = open_page())) return 0;
    load();
    return 0;
}

/* A scoped page: a Coverflow already open (played from, under Now Playing's Go to Artist, issue
 * #73) closes once the new one is up, with an artist page under it: there is one page's state.
 * ponytail: Return then skips it; stack the state if that is missed. */
static int scoped_open(void) {
    void *old = cf.page, *under = artist_under(),
         *page = open_page();
    if (!page) return 0; /* the old page and its state stay */
    if (old) {
        closed(old, 0);
        window_manager_close_window_force(window_manager(), old);
        if (under) window_manager_close_window_force(window_manager(), under);
    }
    cf.page = page;
    return 1;
}

/* Display settings' Artist albums: Coverflow (navigation.c): albums, coverflow_split's, as the
 * covers, which the page owns; 0 when there are none, and albums is the caller's. */
int coverflow_artist(void *albums) {
    if (!deque_size(albums)) return 0;
    void *music = _create_deque("stSongInfo");
    deque_init_copy(music, albums);
    music = music_albums(music);
    if (!deque_size(music) || !scoped_open()) {
        deque_destroy(music);
        return 0;
    }
    deque_destroy(albums);
    albums = music;
    cf.all_albums = cf.albums;
    cf.albums = albums;
    cf.scoped = ARTIST_ALBUMS;
    extern int ringnav_artist_view(void);
    cf.text_albums = ringnav_artist_view() == 2;
    load();
    return 1;
}

/* Go to Album from a Coverflow song (navigation.c's qm_goto) and Era's albums: r's tracks alone, no
 * covers under them, so Return closes the page; 0 when it has none. */
int coverflow_goto_album(void *r) {
    void *t = coverflow_album_tracks(r), *one;
    unsigned n = deque_size(t);
    deque_destroy(t);
    if (!n || !(one = _create_deque("stSongInfo"))) return 0;
    deque_init(one);
    _deque_push_back(one, r);
    if (!scoped_open()) {
        deque_destroy(one);
        return 0;
    }
    cf.all_albums = cf.albums;
    cf.albums = one;
    cf.scoped = ALBUM_TRACKS;
    cf.covers = cf.slide = cf.name = cf.artist = 0;
    cf.album = 0;
    cf.body = widget_factory_create_widget(widget_factory(), "view", cf.page, 0, 0, 375, 290);
    to_tracks(0);
    return 1;
}

/* The open page shows r's album alone (coverflow_goto_album): Go to Album has nowhere new to go. */
int coverflow_shows_album(void *r) {
    return cf.page && cf.scoped == ALBUM_TRACKS && !album_cmp(deque_at(cf.albums, 0), r);
}

/* Library Ratings' Albums (navigation.c): the albums hearts gives 1 to 5 hearts, as covers with the
 * rated Sort card alone; 0 when none is. */
int coverflow_rated(int (*hearts)(void *)) {
    int n;
    void *all = staged(albums, 0, &n), *rated = _create_deque("stSongInfo");
    if (n > 0) all = split(all);
    all = music_albums(all);
    deque_init(rated);
    for (unsigned i = 0; i < deque_size(all); ++i)
        if (hearts(deque_at(all, i))) _deque_push_back(rated, deque_at(all, i));
    deque_destroy(all);
    if (!deque_size(rated) || !scoped_open()) {
        deque_destroy(rated);
        return 0;
    }
    if (!cf.sort_read) BLOB_IO(SORT_FILE, cf.sort, 0), cf.sort_read = 1;
    cf.all_sort = cf.sort, cf.all_stock = cf.stock, cf.all_albums = cf.albums;
    cf.sort = SORT_RATING; /* unless saved */
    BLOB_IO(RATED_SORT_FILE, cf.sort, 0);
    cf.stock = rated, cf.albums = 0, cf.hearts = hearts;
    cf.scoped = RATED_ALBUMS;
    load();
    return 1;
}

/* The queue's playing record, or 0; *pos and *n get its index and the queue length. */
void *queue_now(unsigned *pos, unsigned *n) {
    void *queue = P(mcl_pdeqplaylist, 0);
    *pos = *(volatile unsigned *)MCL_POS;
    *n = queue ? deque_size(queue) : 0;
    return *pos < *n ? deque_at(queue, *pos) : (void *)0;
}

/* r's REC_ALBUM, REC_ARTIST, REC_ALBUM_ARTIST or REC_TITLE as the player parsed it from the file,
 * once it has parsed r's; else the record's own. Folder plays' records, and those of the next folder, which
 * stock queues when a folder play ends (on_player_autochange), carry no tags (issue #7). */
const char *now_tag(void *r, int field) {
    if (!r) return (void *)0;
    if (!tk_strcmp((const char *)g_play_id3_info, P(r, REC_PATH)))
        return (const char *)g_play_id3_info + (field == REC_ALBUM          ? ID3_ALBUM
                                                 : field == REC_TITLE        ? ID3_TITLE
                                                 : field == REC_ALBUM_ARTIST ? ID3_ALBUM_ARTIST
                                                                             : ID3_ARTIST);
    return P(r, field);
}

#if IPOD
#define HOME_PW (375 - HOME_SPLIT_W) /* Split's art panel, 290 high */
/* iPod Home (docs/ipod.md): the playing track's art beside the list. */
static struct {
    void *win, *art, *list, *sets; /* sets: the Settings list, in the list's place while open */
    unsigned key, repaint; /* repaint: the timer of home_repaint */
    /* drift: home_drift's timer; drift_ms: the pan's phase, ms shown */
    unsigned drift, drift_ms, drift_last;
    /* home_tex's zoomed cover (tw 0: the still stock image, which never pans) and the panel's
     * frame, drawn from it at du dv (1/256 px, -1 for none yet) */
    unsigned *tex;
    void *frame;
    int tw, rx, ry, u0, v0, du, dv;
    int clip[4];  /* the canvas clip while the art paints, restored after */
    int clipped;
} home __attribute__((section(".scratch")));
extern int ipod_home_full(void), ipod_home_rockbox(void), home_shortcut_open(void *, void *);
extern const char *ipod_home_shortcut(void);
/* Split's menu width; the art fills the rest. */

/* player_parsecover_thd writes the playing track's cover and then sets g_playcover_type, as Now
 * Playing reads it: 1 embedded, 2 folder image, 4 downloaded; 0 while parsing or stopped, 3 none.
 * Tidal's (5) is keyed by its online URL, never a queue path, so Home leaves it out. */
static const char *const player_covers[] = { 0, "file://" PEQ_ROOT "/tmp/coverpic.jpg",
                                             "file://" PEQ_ROOT "/tmp/externpic.jpg", 0,
                                             "file://" PEQ_ROOT "/tmp/externpic.jpg" };

/* Repaint every window once the paint that changed the art is over: AWTK drops what a paint
 * invalidates when the frame ends, so the new art would reach only regions repainted later. */
static int home_repaint(const void *unused) {
    home.repaint = 0;
    widget_invalidate_force(window_manager(), 0);
    return 0; /* RET_REMOVE */
}

/* One axis of the pan (an iPod classic's menu art): 0 to range px and back over 2 x period ms, in
 * 1/256 px. range is a tenth of a cover within DRIFT_TEXELS (under 100 px), so 32 bits hold it. */
static int drift_at(int range, unsigned period) {
    unsigned t = home.drift_ms % (2 * period);
    return (int)((unsigned)range * 256 * ((t < period ? t : 2 * period - t) >> 4) / (period >> 4));
}

/* Where the panel's top left is in the zoomed cover, in 1/256 px: the old crop, then the pan. */
static int pan_u(void) { return home.u0 * 256 + drift_at(home.rx, DRIFT_X_MS); }
static int pan_v(void) { return home.v0 * 256 + drift_at(home.ry, DRIFT_Y_MS); }

/* A step repaints only the panel, once the pan has moved; coverflow_home_clip draws it. */
static void drift_place(void) {
    if (pan_u() == home.du && pan_v() == home.dv) return;
    static int pane[4] = { HOME_SPLIT_W, 0, HOME_PW, 290 }; /* rect_t, in the parent */
    widget_invalidate_force(P(home.art, W_PARENT), pane);
}

/* The pan runs only while it shows: Home on top and not sliding (its snapshot would not move), the
 * screen lit, a cover that pans (the placeholder does not; Full never paints the art). */
static int drift_live(void) {
    void *wm = window_manager();
    return home.tw && g_backlight_status && !g_lockscreen_pageflag &&
           window_manager_get_top_window(wm) == home.win && !window_manager_is_animating(wm);
}

/* The pan's step; once it no longer shows the timer goes, and the next Home paint arms it again
 * at the same phase. Steps are capped, so a stalled loop never jumps. */
static int home_drift(const void *unused) {
    if (!drift_live()) return (int)(home.drift = 0); /* RET_REMOVE */
    unsigned now = time_now_ms(), step = now - home.drift_last;
    home.drift_last = now;
    /* modulo a whole number of both axes' cycles, so it never wraps mid-cycle */
    home.drift_ms = (home.drift_ms + (step < DRIFT_MAX_STEP_MS ? step : DRIFT_MAX_STEP_MS)) %
                    (2u * DRIFT_X_MS * DRIFT_Y_MS);
    drift_place();
    return 8; /* RET_REPEAT */
}

/* A w x h image's size just covering the panel at its proportions; unknown sizes fill it. */
static void home_fit(unsigned w, unsigned h, int *fw, int *fh) {
    int pw = HOME_PW, ph = 290;
    *fw = pw, *fh = ph;
    if (w && h && w <= 8192 && h <= 8192) {
        if ((unsigned)pw * h > (unsigned)ph * w)
            *fh = (int)(((unsigned)pw * h + w - 1) / w);
        else
            *fw = (int)(((unsigned)ph * w + h - 1) / h);
    }
}

/* The cover at url, box filtered once to its fit zoomed DRIFT_ZOOM_PCT (home.tex, tw x th) and
 * composited over the page as Coverflow's decode does, for the pan to draw from. It pans across
 * the zoom's margin only (rx ry), from the old crop (u0 v0), so its edges never show. 1 when it
 * pans, -1 when nothing loads, 0 for a 16-bit decode or a cover too long for DRIFT_TEXELS, which the
 * stock fill shows still.
 * ponytail: only the playing track's cover, no iPod slideshow of library covers or crossfades;
 * library thumbnails are too small for the panel, so each change would decode a full cover. Light
 * Mode's paper behind a transparent cover changes with the next cover. */
/* [1,2,1]/4 along n opaque texels step apart, in place. home_render's bilinear keeps a whole-pixel
 * frame's texels and mixes a half-pixel one's 50/50, so the pan's sharpness pulsed as its phase
 * turned; texels already this soft read almost alike either way (1-2-1 against 1-3-3-1).
 * ponytail: a constant slight softening of the art, no per-frame cost; a cubic resampler would
 * keep the sharpness and cost every frame. */
void home_soften(unsigned *t, int n, int step) { /* test/coverflow.py */
    unsigned prev = t[0];
    for (int i = 0; i < n; ++i) {
        unsigned c = t[i * step], next = i + 1 < n ? t[(i + 1) * step] : c;
        unsigned rb = ((prev & 0xff00ffu) + 2 * (c & 0xff00ffu) + (next & 0xff00ffu)) >> 2,
                 g = ((prev & 0xff00u) + 2 * (c & 0xff00u) + (next & 0xff00u)) >> 2;
        prev = c;
        t[i * step] = 0xff000000u | (rb & 0xff00ffu) | (g & 0xff00u);
    }
}

static int home_tex(const char *url) {
    static const unsigned char at[4][4] = BITMAP_RGBA_AT;
    unsigned bitmap[64]; /* bitmap_t */
    if (widget_load_image(home.art, url, bitmap)) return -1;
    unsigned w = bitmap[0], h = bitmap[1], format = ((unsigned short *)bitmap)[7] - 1u;
    int fw, fh;
    home_fit(w, h, &fw, &fh);
    int tw = fw * DRIFT_ZOOM_PCT / 100, th = fh * DRIFT_ZOOM_PCT / 100;
    const unsigned char *data = 0;
    if (!home.frame) home.frame = opaque_frame(HOME_PW, 290);
    if (home.frame && format < 4 && w && h && w <= 8192 && h <= 8192 && tw <= DRIFT_TEXELS / th) {
        free(home.tex); /* a row and a texel more: home_render's last neighbours, at weight 0 */
        home.tex = calloc((unsigned)(th + 1) * (unsigned)tw + 1, 4);
        data = home.tex ? bitmap_lock_buffer_for_read(bitmap) : 0;
    }
    if (data) {
        const unsigned char *o = at[format];
        unsigned stride = bitmap_get_line_length(bitmap), paper = ipod_light() ? LIGHT_PAPER : 0;
        unsigned *out = home.tex;
        for (int y = 0; y < th; ++y) {
            unsigned y0 = (unsigned)y * h / (unsigned)th, y1 = (unsigned)(y + 1) * h / (unsigned)th;
            y1 += y1 == y0;
            for (int x = 0; x < tw; ++x) {
                unsigned x0 = (unsigned)x * w / (unsigned)tw,
                         x1 = (unsigned)(x + 1) * w / (unsigned)tw;
                x1 += x1 == x0;
                /* alpha-weighted sums, so only the average divides; at most 4 x 4 samples, so a
                 * huge embedded cover filters as quickly as a small one */
                unsigned r = 0, g = 0, b = 0, a = 0, n = 0, xs = (x1 - x0 + 3) / 4 * 4,
                         ys = (y1 - y0 + 3) / 4;
                for (unsigned sy = y0; sy < y1; sy += ys)
                    for (const unsigned char *px = data + sy * stride + x0 * 4,
                                             *end = data + sy * stride + x1 * 4;
                         px < end; px += xs, n += 255) {
                        unsigned k = px[o[3]];
                        r += px[o[0]] * k, g += px[o[1]] * k, b += px[o[2]] * k, a += k;
                    }
                unsigned bg = paper * (n - a);
                *out++ = 0xff000000u | (r + bg) / n | (g + bg) / n << 8 | (b + bg) / n << 16;
            }
        }
        bitmap_unlock_buffer(bitmap);
        for (int y = 0; y < th; ++y) home_soften(home.tex + y * tw, tw, 1);
        for (int x = 0; x < tw; ++x) home_soften(home.tex + x, th, tw);
        home.tw = tw, home.rx = tw - fw, home.ry = th - fh;
        home.u0 = (fw - HOME_PW) / 2, home.v0 = (fh - 290) / 2, home.du = -1;
    }
    widget_unload_image(home.art, bitmap);
    return data != 0;
}

/* The panel from the zoomed cover at u, v (1/256 px), bilinear, so the pan moves by fractions of
 * a pixel: in whole pixels its 1.5 a second jerked. Opaque, so red and blue blend in one word. */
static void home_render(unsigned *d, int pitch, int u, int v) {
    unsigned fu = (unsigned)u & 255, fv = (unsigned)v & 255, w00 = (256 - fu) * (256 - fv) >> 8,
             w01 = fu * (256 - fv) >> 8, w10 = (256 - fu) * fv >> 8, w11 = 256 - w00 - w01 - w10;
    for (int y = 0; y < 290; ++y, d += pitch) {
        const unsigned *a = home.tex + ((v >> 8) + y) * home.tw + (u >> 8), *b = a + home.tw;
        for (int x = 0; x < HOME_PW; ++x, ++a, ++b) {
            unsigned rb = (a[0] & 0xff00ffu) * w00 + (a[1] & 0xff00ffu) * w01 +
                          (b[0] & 0xff00ffu) * w10 + (b[1] & 0xff00ffu) * w11,
                     g = (a[0] & 0xff00u) * w00 + (a[1] & 0xff00u) * w01 + (b[0] & 0xff00u) * w10 +
                         (b[1] & 0xff00u) * w11;
            d[x] = 0xff000000u | (rb >> 8 & 0xff00ffu) | (g >> 8 & 0xff00u);
        }
    }
}

/* The pan's frame over the art (its image is empty), rendered again only once the pan moved. */
static void home_paint(void *canvas) {
    int u = pan_u(), v = pan_v(), r[4] = { 0, 0, HOME_PW, 290 };
    if (u != home.du || v != home.dv) {
        unsigned *d = (unsigned *)bitmap_lock_buffer_for_write(home.frame);
        if (!d) return;
        home_render(d, (int)(bitmap_get_line_length(home.frame) / 4), u, v);
        bitmap_unlock_buffer(home.frame);
        home.du = u, home.dv = v;
    }
    canvas_draw_image(canvas, home.frame, r, r);
}

/* The player's cover, else the Coverflow cache of the track's album (now_tag), else the
 * placeholder. The player's files belong to the track whose path it copies to g_lastcover_url
 * after writing them, so right after a track change they count only once that is this track. Runs
 * whenever a window or the status bar paints (at least once a second), for Home in Split, and
 * reloads only when the track, the cover it can use or its parsed tags change; home_repaint then
 * shows the new art whole. */
void coverflow_home_art(void *top) {
    if (!home.art || top != home.win || !widget_get_visible(home.art)) return;
    if (!home.drift && drift_live()) {
        home.drift_last = time_now_ms();
        home.drift = timer_add(home_drift, (void *)0, DRIFT_TICK_MS);
    }
    unsigned pos, n;
    void *r = queue_now(&pos, &n);
    const char *path = r ? P(r, REC_PATH) : (void *)0;
    unsigned char type = path && !tk_strcmp((const char *)g_lastcover_url, path) ? g_playcover_type : 0;
    unsigned album = tags_key(now_tag(r, REC_ARTIST), now_tag(r, REC_ALBUM));
    unsigned key = hash_bytes(hash_bytes(fnv(FNV_SEED, (const unsigned char *)path), &type, 1),
                              (const unsigned char *)&album, sizeof(album));
    if (key == home.key) return;
    home.key = key;
    const char *cover = type < sizeof(player_covers) / sizeof(*player_covers) ? player_covers[type] : 0;
    char url[600] = "file://";
    if (r) art_path(url + 7, album, "");
    unsigned size[2] = { 0, 0 };
    int fw = HOME_PW, fh = 290;
    home.tw = 0;
    const char *src = cover;
    /* the player writes every track's cover to the same file: drop AWTK's cached bitmap of the
     * last track's, or the new track shows it (Enlarged Art does the same) */
    if (cover) image_manager_unload_bitmap_by_name(image_manager(), cover);
    int pans = cover ? home_tex(cover) : -1;
    if (pans < 0 && r) pans = home_tex(url), src = url;
    if (pans > 0) {
        image_base_set_image(home.art, ""); /* home_paint draws it */
    } else {
        /* still, sized to the image's fit and centred, so the native fill draws it whole and the
         * clip crops it evenly */
        if (!(pans == 0 && image_show(home.art, src, size)) &&
            !image_show(home.art, HOME_PLACEHOLDER, size))
            image_base_set_image(home.art, HOME_PLACEHOLDER);
        home_fit(size[0], size[1], &fw, &fh);
    }
    widget_move_resize(home.art, HOME_SPLIT_W + (HOME_PW - fw) / 2, (290 - fh) / 2, fw, fh);
    rearm(&home.repaint, home_repaint, 0);
}

/* The art paints only inside its panel and the lists only left of it, so a sliding list
 * never covers the art: ringnav_paint_bg narrows the canvas clip (screen coordinates; the canvas
 * origin is the widget's) before stock draws it, and ringnav_paint puts the old clip back after its
 * children, the pan's frame painted inside it first. */
void coverflow_home_clip(void *w, void *canvas, int begin) {
    if (!w || (w != home.art && w != home.list && w != home.sets)) return;
    if (!begin) {
        if (w == home.art && home.tw) home_paint(canvas);
        if (home.clipped) canvas_set_clip_rect(canvas, home.clip);
        home.clipped = 0;
        return;
    }
    int clip[4], art = w == home.art, x = HOME_SPLIT_W, pane = widget_get_visible(home.art) ? x : 375;
    clip_within(canvas, home.clip, clip, I(canvas, CANVAS_X) - I(w, W_X) + (art ? x : 0),
                I(canvas, CANVAS_Y) - I(w, W_Y) + (art ? 0 : I(w, W_Y)), art ? 375 - x : pane,
                art ? 290 : I(w, W_H));
    canvas_set_clip_rect(canvas, clip);
    home.clipped = 1;
}

/* Whether w is in one of Home's menus (Minimal draws their labels, paint_caps). */
int coverflow_home_owns(void *w) {
    for (; w && home.list; w = P(w, W_PARENT))
        if (w == home.list || w == home.sets) return 1;
    return 0;
}

/* Both menus' lists span outer, their rows and tap targets inner; labels start at x and end end
 * before the row's edge (Classic: before the chevron). */
static void home_width(void *w, int outer, int inner, int x, int end, int depth) {
    widget_move_resize(w, I(w, W_X), depth ? I(w, W_Y) : ipod_classic() ? 0 : HOME_TOP,
                       depth < 2 ? outer : inner, I(w, W_H));
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i) {
        void *child = widget_get_child(w, i);
        if (!tk_strcmp(widget_get_type(child), "hscroll_label")) {
            widget_move_resize(child, x, I(child, W_Y), inner - x - end, I(child, W_H));
            bold_label(child, -1); /* Classic's bold; Minimal paints its own (paint_caps) */
        }
        else
            home_width(child, outer, inner, x, end, depth + 1);
    }
}

/* Stored HOME=0/1 is Split/Full in both themes: Split halves the screen, the art in the right
 * panel; Full hides the art and widens the list, its rows to HOME_FULL_ROW (Minimal) or
 * HOME_CLASSIC_ROW. Hidden Rockbox is restacked last because the native layout includes hidden
 * rows, and made insensitive, as hidden rows keep their taps. */
void coverflow_home_layout(void) {
    int full = ipod_home_full(), classic = ipod_classic();
    /* The art goes back to the panel; Split's next paint resumes the pan. */
    stop_timer(&home.drift);
    if (!home.list) return;
    void *rockbox = widget_lookup(home.win, "btn_rockbox", 1);
    if (rockbox) {
        int shown = ipod_home_rockbox();
        widget_restack(rockbox, shown ? 4 : 6); /* after Folder, else last */
        widget_set_visible(rockbox, shown, 0);
        widget_set_sensitive(rockbox, shown); /* hidden, it would still take Streaming's taps */
        widget_set_text_utf8(widget_lookup(home.win, "label_rockbox", 1), ipod_home_shortcut());
    }
    int split = HOME_SPLIT_W, outer = full ? 375 : split,
        inner = !full ? split : classic ? HOME_CLASSIC_ROW : HOME_FULL_ROW,
        x = classic ? HOME_CLASSIC_TEXT_X : HOME_DOT_X + HOME_DOT + HOME_DOT_GAP,
        end = classic ? HOME_CLASSIC_LABEL_END : full ? HOME_LABEL_END : HOME_SPLIT_LABEL_END;
    home_width(home.list, outer, inner, x, end, 0);
    if (home.sets) home_width(home.sets, outer, inner, x, end, 0);
    widget_move_resize(home.art, split, 0, 375 - split, 290);
    widget_set_visible(home.art, !full, 0);
    home.key = ~0u; /* Split shows the current cover again, next frame if laid out mid-paint */
    rearm(&home.repaint, home_repaint, 0);
}

/* Home's Settings row, as an iPod's submenu: the Settings list (stock's Playback and System
 * setting rows, which stock binds) slides in from the right over the list's place, the art staying;
 * Return (coverflow_home_back) slides the list back in from the left. Each list keeps its row. */
static void home_swap(void *from, void *to, int dx) {
    widget_set_visible(from, 0, 0);
    widget_set_sensitive(from, 0); /* hidden rows keep their taps: only the shown list takes them */
    widget_move_resize(to, dx, I(to, W_Y), I(to, W_W), I(to, W_H));
    widget_set_visible(to, 1, 0);
    widget_set_sensitive(to, 1);
    void *a = widget_animator_prop_create(to, PAGE_SLIDE_MS, 0, SLIDE_EASING, "x");
    if (!a) {
        widget_move_resize(to, 0, I(to, W_Y), I(to, W_W), I(to, W_H));
        return;
    }
    widget_animator_prop_set_params(a, dx, 0);
    widget_animator_start(a);
}

static int home_settings(void *ctx, void *event) {
    if (home.sets && !widget_get_visible(home.sets)) home_swap(home.list, home.sets, I(home.list, W_W));
    return 0;
}

/* Return on Home with Settings open: 1 when it went back to the list. */
int coverflow_home_back(void *top) {
    if (!home.sets || top != home.win || !widget_get_visible(home.sets)) return 0;
    home_swap(home.sets, home.list, -I(home.list, W_W));
    return 1;
}
#endif

/* home_page_init: stock binds the name-matched img_* cards, then the Coverflow card binds here,
 * on its image as stock does, whatever stock returned. */
int coverflow_home(void *win, void *ctx) {
    int result = stock_home_trampoline(win, ctx);
    widget_on(widget_lookup(win, "img_coverflow", 1), EVT_CLICK, coverflow_open, 0);
#if IPOD
    widget_on(widget_lookup(win, "img_rockbox", 1), EVT_CLICK, home_shortcut_open, 0);
    widget_on(widget_lookup(win, "img_settings", 1), EVT_CLICK, home_settings, 0);
    home.win = win;
    void *art = widget_lookup(win, "img_homeart", 1);
    home.art = art;
    home.clipped = 0;
    /* The fitted cover reaches under the list; taps there must still find the rows. */
    if (art) widget_set_sensitive(art, 0);
    void *list = widget_lookup(win, "list_view_home", 1);
    home.list = list; /* a new Home window's own, so still the asset's width */
    home.sets = widget_lookup(win, "list_view_homeset", 1);
    if (home.sets) widget_set_sensitive(home.sets, 0);
    coverflow_home_layout();
#endif
    return result;
}
