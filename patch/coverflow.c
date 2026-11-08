/* Coverflow (docs/internals.md): the stock Local Music albums on a stock slide_menu, the way
 * PictureFlow reads Rockbox's database. No tag reads: albums and tracks come from the stock library
 * queries, and the only thing Coverflow owns is a thumbnail cache, built on a modal screen by one
 * pthread that touches only files, the two stock art locks and the volatile counters below. */
#include "coverflow.h"

/* On the card, beside stock's own cover cache (/mnt/mmc/.sldp). */
#define ART_DIR PEQ_ROOT "/mnt/mmc/.coverflow"
#define ART_SIZE 160
#define LAST_ALBUM ART_DIR "/album" /* the centre album's key, so a reboot opens on it */
#define MIN_FREE_MB 16 /* no new cache files below this much free space on the card */
#define ART_NEAR 3 /* real art only this many covers either side, like PictureFlow's cache */
/* Home's without a theme's pane (home_pane_paint), at 290px (stock's: 110) */
#define HOME_PLACEHOLDER "default_album_home"

extern int stock_home_trampoline(void *win, void *ctx), stock_scan_all_trampoline(void *, void *),
    stock_scan_folder_trampoline(void *, void *), stock_delete_song_trampoline(void *, void *),
    stock_file_created_trampoline(const char *),
    stock_album_cover_trampoline(const char *, const char *, int, int);

enum { PREPARING, COVERS, TRACKS };
cf_page_t cf __attribute__((section(".scratch")));

static int pick(void *ctx, void *event);

/* The album list is kept across opens until songtable changes. Only these three stock functions
 * write it; each moves the generation before and after it runs, so a list queried meanwhile is
 * never kept. The scans run on stock's scan thread, and first walk the card for folder markers. */
volatile unsigned library_gen __attribute__((section(".scratch"))); /* also Most Played's */
static int library_write(int (*stock)(void *, void *), void *a, void *b) {
    ++library_gen;
    int result = stock(a, b);
    ++library_gen;
    return result;
}
int coverflow_scan_all(void *a, void *b) {
    library_scope_scan();
    return library_write(stock_scan_all_trampoline, a, b);
}
/* toolsGetFileCreateTime (0x5bddc4) dates a song (time_create, Recently Added's order) by stat's
 * st_ctime, on FAT32 the directory entry's creation time, but by st_atime on exFAT cards, where
 * stat has no creation time; every read moves that, so a full update dated the whole library to
 * its own day (#170, #188). statx reports the creation stamp exFAT keeps (stock's kernel has the
 * mainline driver); the o32 syscall itself, as demo imports no statx. docs/internals.md has the
 * rest. ponytail: a file whose filesystem reports no stamp takes stock's date and drifts as
 * before. */
#ifndef PEQ_HOST
__asm__(".globl ringnav_statx\nringnav_statx:\nli $v0, 4366\nsyscall\njr $ra\nnop\n");
extern int ringnav_statx(int dirfd, const char *path, int flags, unsigned mask, void *buf);
int ringnav_file_created(const char *path) {
    unsigned sx[64]; /* struct statx: stx_mask first, stx_btime.tv_sec at byte 80 */
    if (ringnav_statx(-100, path, 0, 0x800, sx) == 0 && (sx[0] & 0x800) && sx[20])
        return (int)sx[20];
    return stock_file_created_trampoline(path);
}
#endif
int coverflow_scan_folder(void *a, void *b) {
    library_scope_scan();
    return library_write(stock_scan_folder_trampoline, a, b);
}
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

unsigned album_key(void *r) { return tags_key(P(r, REC_ARTIST), P(r, REC_ALBUM)); }

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
/* Runs that never reached ffmpeg's own exit, from any thread (counted atomically, so none is
 * lost); build_art and photos.c compare before and after. */
volatile unsigned ff_kills __attribute__((section(".scratch")));
static int ff_run(const char *src, const char *dst, int w, int h, int *exited) {
    char vf[80], tmp[1040];
    tk_snprintf(vf, sizeof(vf), "scale=%d:%d:force_original_aspect_ratio=decrease", w, h);
    tk_snprintf(tmp, sizeof(tmp), "%s.ff", dst);
    int status = -1, pid = fork();
    if (!pid) {
        child_oom_first(); /* its one picture can need tens of MB (#169) */
        execl(ART_FFMPEG, ART_FFMPEG, "-nostdin", "-v", "quiet", "-xerror", "-i", src, "-frames:v", "1",
              "-vf", vf, "-pix_fmt", "yuvj420p", "-q:v", "3", "-f", "mjpeg", "-y", tmp, (char *)0);
        child_exit(127);
    }
    while (pid > 0 && waitpid(pid, &status, 0) < 0 && *__errno_location() == 4) { /* EINTR: reap it */
    }
    /* WIFEXITED, less execl's own failure (127: out of memory, as ffmpeg is stock's); never for -1 */
    *exited = !(status & 0x7f) && ((status >> 8) & 0xff) != 127;
    if (!*exited) __atomic_fetch_add(&ff_kills, 1, __ATOMIC_RELAXED);
    if (!status && file_size(tmp) > 0 && !rename(tmp, dst)) return 1;
    unlink(tmp);
    return 0;
}

static int ff_thumb(const char *src, const char *dst, int w, int h) {
    int exited;
    return ff_run(src, dst, w, h, &exited);
}

static unsigned be16(const unsigned char *p) { return p[0] << 8 | p[1]; }

/* Whether stock's decoder would hold path's whole picture in demo, which the OOM killer would then
 * take (#216), read from its header: a PNG over 1024 px either way (its reader, 0x5b66f8, expands
 * the image with png_read_png before its own 1024 px test) or a progressive JPEG over 1024 px
 * (libjpeg keeps a full-size coefficient buffer at any scale). Magic bytes as toolsGetPictureInfo
 * tells them; the JPEG's frame header is looked for in its first 64 segments.
 * ponytail: only SOF2, as ffmpeg 4.2 decodes no arithmetic (SOF10) or hierarchical frames; a
 * progressive cover ffmpeg's -xerror rejects (cut short) no longer falls back to stock. */
static int big_picture(const char *path) {
    unsigned char b[24];
    void *f = fopen(path, "rb");
    int got = f && fread(b, 1, sizeof b, f) == sizeof b, big = 0;
    if (got && b[0] == 0x89 && b[1] == 'P' && !memcmp(b + 12, "IHDR", 4)) /* its width and height */
        big = be16(b + 16) || be16(b + 18) > 1024 || be16(b + 20) || be16(b + 22) > 1024;
    else if (got && b[0] == 0xff && b[1] == 0xd8)
        for (long at = 2, n = 0; n < 64 && !fseek(f, at, 0) && fread(b, 1, 9, f) == 9 &&
                                 b[0] == 0xff && b[1] != 0xda; /* image data follows */
             ++n, at += 2 + be16(b + 2))
            if (b[1] >> 4 == 0xc && b[1] != 0xc4 && b[1] != 0xc8 && b[1] != 0xcc) {
                /* a frame header, SOF0 to SOF15: SOF2 is the Huffman progressive one */
                big = b[1] == 0xc2 && (be16(b + 5) > 1024 || be16(b + 7) > 1024);
                break;
            }
    if (f) fclose(f);
    return big;
}

/* stock's thumbnailer, under the lock every stock caller holds, else ffmpeg; shared with photos.c.
 * A picture stock would decode whole in demo goes to ffmpeg alone, whose child the kernel kills
 * first. */
int thumb(const char *src, const char *dst, int w, int h) {
    int ok = 0;
    if (!big_picture(src)) {
        pthread_mutex_lock((void *)parse_cover_mutex);
        ok = toolsThumbSpecCover(src, dst, w, h) == 1;
        pthread_mutex_unlock((void *)parse_cover_mutex);
    }
    return ok || (!access(src, 4) && ff_thumb(src, dst, w, h)); /* none for a missing file */
}

/* Which albums share a folder with another album (job_t.shared), one byte per album; 0 without
 * memory. Albums with a path are sorted by folder (its FNV hash, then the folder itself, then the
 * album key), so each folder's albums sit together, and a folder holding two keys marks all of
 * them. A scan of every album per album here was albums squared on the UI thread, the likely
 * (inferred, not reproduced) cause of a big library's first open stalling past the watchdog. */
typedef struct {
    unsigned hash, len, key, idx;
    const char *path;
} folder_t;
static int folder_before(const void *a, const void *b) {
    const folder_t *x = a, *y = b;
    int c = x->hash != y->hash ? (x->hash < y->hash ? -1 : 1)
            : x->len != y->len ? (x->len < y->len ? -1 : 1)
                               : memcmp(x->path, y->path, x->len);
    return c ? c : (x->key > y->key) - (x->key < y->key);
}
/* noinline: test/patch.py calls it */
__attribute__((noinline)) static unsigned char *folders_shared(void *albums, unsigned count) {
    folder_t *v = calloc(count + 1, sizeof *v);
    unsigned char *shared = calloc(count + 1, 1);
    unsigned n = 0;
    for (unsigned i = 0; v && shared && i < count; ++i) {
        void *r = deque_at(albums, i);
        const char *p = P(r, REC_PATH), *s = p ? strrchr(p, '/') : 0;
        if (s)
            v[n++] = (folder_t){ hash_bytes(FNV_SEED, (const unsigned char *)p, s - p), s - p,
                                 album_key(r), i, p };
    }
    if (n) qsort(v, n, sizeof *v, folder_before);
    for (unsigned a = 0, b; a < n; a = b) {
        for (b = a + 1; b < n && v[b].hash == v[a].hash && v[b].len == v[a].len &&
                        !memcmp(v[b].path, v[a].path, v[a].len);
             ++b);
        for (unsigned k = a; v[a].key != v[b - 1].key && k < b; ++k) shared[v[k].idx] = 1;
    }
    if (!v) free(shared), shared = 0;
    free(v);
    return shared;
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

/* toolsGetAlbumCover's entry, so every embedded-art read: player_parsecover_thd's Now Playing art,
 * the GRID_COVER calls and build_art. libtag_c's taglib_album_picture gives an .ogg to
 * get_oggfile_albumpic, XiphComment::pictureList after TagLib's whole parse, about 4 times the
 * comment header (docs/internals.md#oversized-ogg-tags). One over OGG_TAGS_MAX gets none from
 * stock, so its caller's fallback (coverflow_player_cover, grid_cover, build_art) hands it to
 * ffmpeg, which the kernel kills before demo. Stock reads no .opus or .oga picture, so those are
 * not measured. */
#ifndef PEQ_HOST
int ringnav_album_cover(const char *track, const char *dst, int w, int h) {
    const char *dot = strrchr(track, '.');
    if (dot && !strcasecmp(dot, ".ogg") && ogg_tags(track, 0)) return 0;
    return stock_album_cover_trampoline(track, dst, w, h);
}
#endif

int coverflow_grid_cover(const char *track, const char *dst, int w, int h) {
    return grid_cover(track, dst, w, h, 0);
}

int coverflow_album_cover(const char *track, const char *dst, int w, int h) {
    return grid_cover(track, dst, w, h, 1);
}

/* Folder view's folder row: stock tries the folder's songs in directory order, the order they
 * were copied to the card, and keeps the first one's embedded art, so an album copied out of
 * order showed a later song's cover. The folder's first song by name, as Folder view lists it,
 * is tried first (not a dot file: a Mac's ._ copies sort first). Stock calls once a song until
 * one has art, so the folder's first song, and whether it had none, are kept for the walk's
 * next call; a hit ends the walk, and the next one lists the folder again. ponytail: only songs
 * with the extension of stock's are compared, by strcasecmp rather than the list's own
 * comparator; a folder with no art at all keeps its listing until another folder is shown. */
int coverflow_folder_cover(const char *track, const char *dst, int w, int h) {
    static struct {
        char dir[512], first[512];
        int bare; /* first has no art */
    } *f __attribute__((section(".scratch")));
    const char *slash = strrchr(track, '/'), *ext = strrchr(track, '.');
    unsigned n = slash ? (unsigned)(slash - track) : 0;
    if (!f) f = calloc(1, sizeof *f); /* heap, as the scratch page is nearly full */
    if (!f || !n || n >= sizeof f->dir || !ext || ext < slash) return grid_cover(track, dst, w, h, 0);
    if (memcmp(f->dir, track, n) || f->dir[n]) {
        char low[256] = "";
        memcpy(f->dir, track, n), f->dir[n] = 0, f->first[0] = 0, f->bare = 0;
        void *d = opendir(f->dir);
        for (struct dirent *e; d && (e = readdir(d));) {
            const char *x = strrchr(e->d_name, '.');
            if ((e->d_type == 8 || !e->d_type) && e->d_name[0] != '.' && x && !strcasecmp(x, ext) &&
                (!low[0] || strcasecmp(e->d_name, low) < 0))
                tk_snprintf(low, sizeof low, "%s", e->d_name);
        }
        if (d) closedir(d);
        if (low[0] && tk_snprintf(f->first, sizeof f->first, "%s/%s", f->dir, low) >= (int)sizeof f->first)
            f->first[0] = 0; /* too long: stock's song alone */
    }
    if (f->first[0] && !f->bare && grid_cover(f->first, dst, w, h, 0)) return f->dir[0] = 0, 1;
    f->bare = 1;
    if (!strcmp(f->first, track) || !grid_cover(track, dst, w, h, 0)) return 0;
    return f->dir[0] = 0, 1;
}

/* One album: embedded art, then beside_art, then ffmpeg's read of the embedded art, last as it
 * costs a process for every album without art; written to .tmp and renamed. An empty file marks
 * "no art", so the placeholder shows and the album is not retried until Refresh Coverflow; a run
 * ffmpeg did not finish leaves none. ponytail: an album whose ffmpeg is killed every time costs
 * one kill per open. */
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
    unsigned kills = ff_kills;
    ok = ok || beside_art(j->track, tmp, ART_SIZE, ART_SIZE, j->shared, thumb) ||
         ff_thumb(j->track, tmp, ART_SIZE, ART_SIZE);
    if (ok && !rename(tmp, dst)) return;
    unlink(tmp);
    /* No marker when an ffmpeg run meanwhile never reached its own exit (a refused fork, or killed:
     * the kernel takes it first when memory runs out, #169; another thread's run counts too, a
     * retry at worst) or storage is gone: the next open retries. */
    if (kills != ff_kills || access(j->track, 0)) return;
    void *f = fopen(dst, "w");
    if (f) fclose(f);
}

static unsigned art_counts[2]; /* crash_note's, every 50th album */
static void *worker(void *unused) {
    while (cf.done < cf.total && !cf.cancel) {
        crash_note(cf.jobs[cf.done].track, "/tmp/q2-art-file", "/tmp/q2-art.log", 50, art_counts);
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

/* A stock library query run into the caller's deque, the staging pointer on it so it leaves no trace.
 * No copies: two of a 47,000-song library's rows got demo OOM-killed, with 128 MB and no swap. Stock
 * writes the pointer only at init and reads it afresh at each use; every caller is on the UI thread,
 * as an overlap could leave the pointer on a deque its owner destroyed. */
int staged_into(void *into, int (*query)(void *), void *arg) {
    void *dir = P(tools_pdeq_directory, 0);
    P(tools_pdeq_directory, 0) = into;
    int n = query(arg);
    P(tools_pdeq_directory, 0) = dir;
    return n;
}

/* Its rows (*count its result) in a deque of their own; shared with navigation.c's queue menu. */
void *staged(int (*query)(void *), void *arg, int *count) {
    void *out = _create_deque("stSongInfo");
    deque_init(out);
    *count = staged_into(out, query, arg);
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
#define ALBUM_SQL "select id,album,songer," ALBUM_TRACK ",albumsonger from songtable" ALBUM_GROUP
#define MUSIC_ALBUM_SQL "select case when ifnull(album,'')='' then -1 else id end," \
    "case when ifnull(album,'')='' then 'Unknown Album' else album end," \
    "songer," ALBUM_TRACK ",albumsonger from songtable where " \
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

/* Letters mean something on w: the whole library's covers, sorted by album or artist. */
int lettered(void *w) {
    return w && w == cf.slide && cf.screen == COVERS && cf.sort <= SORT_ARTIST && !cf.scoped;
}

/* Utility cards and wrap edges remain individual stops. Reverse lands at a group's start. */
int coverflow_jump(void *w, int from, int dir, unsigned *letter) {
    int n = cf.albums ? (int)deque_size(cf.albums) : 0;
    *letter = 0;
    if (!lettered(w) || from < 0 || from >= n) return 1;
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
    motion_open(page, name);
    return page;
}

#define TITLE_ON_SHOW 0 /* page_title's n when the caller writes the title again */

/* A page's title row, the theme's height. 0: the bar holds the caption, and this hidden label is
 * what it reads, unless the page also needs it as a first line, a row high: with no rows (n 0),
 * when it's all the page says, or when it doesn't fit the bar's box (bar_title_fits). Shared with
 * photos.c. */
void *page_title(void *body, const char *caption, int n) {
    const theme_t *T = theme();
    int x = T->page_x, w = 375 - 2 * x;
    void *title = text(body, x, 0, w, T->page_title_h);
    widget_set_text_utf8(title, caption);
    if (T->page_title_h) return title;
    widget_set_name(title, PAGE_TITLE);
    if (n && bar_title_fits(title)) {
        widget_set_visible(title, 0, 0);
    } else {
        widget_use_style(title, "s_scrlabel_white20l");
        widget_move_resize(title, x, 0, w, T->page_row);
        theme_font(title, ROLE_TITLE, FONT_ROW);
    }
    return title;
}

/* A list view in parent from y, w wide and at most h high, for n item_h rows; its scroll view. */
static void *rows_view(void *parent, int y, int w, int h, int n, int item_h) {
    int rows = n * item_h < h ? n * item_h : h;
    void *lv = list_view_create(parent, 0, y, w, rows);
    widget_set_prop_int(lv, "item_height", item_h);
    /* The theme's default list_view is a light card; stock pages paint theirs black inline. */
    widget_set_prop_int(lv, "style:normal:bg_color", (int)0xff000000u);
    widget_set_prop_int(lv, "style:normal:border_color", 0);
    void *view = scroll_view_create(lv, 0, 0, w, rows);
    widget_set_prop_int(view, "yslidable", 1);
    widget_set_prop_int(view, "xslidable", 0);
    widget_set_prop_int(view, "virtual_h", n * item_h);
    return view;
}

/* The peq_ui.c page: a title bar (*title, unless 0: its caller writes it again, so it stays on
 * show) over n item_h rows (page_h) in body, down to the page's bottom (the last row's selection
 * meets the screen's edge) or shrunk so the list's white background never shows below a short list.
 * Shared with photos.c. */
void *page_list(void *page, void *body, void **title, const char *caption, int n, int item_h) {
    int h = widget_get_prop_int(page, "h", client_h());
    item_h = page_h(item_h);
    widget_destroy_children(body);
    widget_set_visible(body, 1, 0);
    void *t = page_title(body, caption, title ? TITLE_ON_SHOW : n);
    if (title) *title = t;
    void *view = rows_view(body, I(t, W_H), 375, h - I(t, W_H), n, item_h);
    if (!theme()->page_title_h) bar_title_sync(page);
    widget_invalidate_force(page, 0);
    return view;
}

/* page_list for a page with a name of its own and a message for no rows (page_empty): the bar still
 * reads the name. */
void *page_list_or(void *page, void *body, const char *name, const char *none, int n, int item_h) {
    void *view = page_list(page, body, 0, n ? name : none, n, item_h);
    if (!n) page_empty(body, name);
    return view;
}

/* The stock page_list heights, 48 a row and 64 a row with a detail, as the theme's. */
int page_h(int h) {
    const theme_t *T = theme();
    return h == 64 ? T->page_detail : h == 48 ? T->page_row : h;
}

/* The theme's page rows' text column. */
int page_x(void) { return theme()->page_x; }

/* A page_list caption written again, and the bar's when it holds it. */
void page_retitle(void *page, void *title, const char *caption) {
    widget_set_text_utf8(title, caption);
    if (!theme()->page_title_h) bar_title_sync(page);
}

static void *list(const char *title, int n) {
    return page_list(cf.page, cf.body, &cf.title, title, n, 48);
}

/* One row of a page_list (48px, the theme's page_h) or, with a detail (navigation.c's Most Played),
 * a taller one: the caption over the detail in 16px #AAAAAA, stock's s_scrlabel_gray24l grey, in
 * the theme's boxes and fonts. Under a theme's separators an empty detail centres the caption above
 * them, its label kept for whoever writes it. iPod left-aligns them, as every stock list. Returns
 * the row. Shared with photos.c. */
void *page_row_detail(void *view, int index, const char *caption, const char *detail,
                      int (*click)(void *, void *)) {
    const theme_t *T = theme();
    int h = page_h(detail ? 64 : 48), x = T->page_x, w = 375 - 2 * x,
        sep = detail && !*detail ? T->sep[0].n : 0;
    void *item = list_item_create(view, 0, index * h, 375, h);
    widget_use_style(item, "s_listitem_black");
    void *label = text(item, x, !detail || sep ? 0 : T->page_cap_y, w,
                       !detail ? h
                       : sep   ? h - sep
                               : T->page_cap_h);
    widget_use_style(label, "s_scrlabel_white20l");
    widget_set_text_utf8(label, caption);
    theme_font(label, detail ? ROLE_TALL : ROLE_TITLE, FONT_ROW);
    if (detail) {
        if (T->sep[0].n) widget_set_name(item, PAGE_TALL);
        label = text(item, x, T->page_det_y, w, T->page_det_h);
        widget_use_style(label, "s_scrlabel_white20l");
        widget_set_prop_int(label, "style:normal:text_color", (int)0xffaaaaaau);
        widget_set_prop_int(label, "style:normal:font_size", 16);
        widget_set_text_utf8(label, detail);
        theme_font(label, ROLE_META, FONT_ROW);
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
    void *into = image_create(item, 302, 0, 50, h);
    image_base_set_image(into, "list_into");
    if (theme()->chevron_sel) widget_set_visible(into, 0, 0); /* the selection's chevron instead */
}

/* A page_row_detail row that opens another page, with row_into's chevron. An empty detail centres
 * the caption in its 64px row, beside the chevron. */
void *page_row_detail_into(void *view, int index, const char *caption, const char *detail,
                           int (*click)(void *, void *)) {
    void *item = page_row_detail(view, index, caption, detail, click);
    const theme_t *T = theme();
    int h = page_h(detail ? 64 : 48);
    if (detail && !*detail)
        widget_move_resize(widget_get_child(item, 0), T->page_x, 0, 375 - 2 * T->page_x,
                           h - T->sep[0].n);
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
/* the active theme's Cover Flow values, copied at page open (cf_restyle) */
cf_style_t cfs __attribute__((section(".scratch")));
/* with them, cf_restyle's: a side cover's sine, and its near edge's screen x (1/16 px), at t = 1 */
static int cf_s1 __attribute__((section(".scratch")));
static int cf_rest16 __attribute__((section(".scratch")));
#define CF_SWING_T (CF_ONE * 45 / 100)
#define CF_EYE16 (16 * cfs.eye)
#define CF_HALF16 (16 * ART_SIZE / 2)
/* the horizon every cover shares, and a cover h high (1/256 px)'s top row on it */
#define CF_TOP256(h) (256 * cfs.top + 40960 * cfs.horizon / 100 - (h) * cfs.horizon / 100)


int cf_floor(float x) {
    int i = (int)x;
    return i - (x < (float)i);
}
int cf_round(float x) { return cf_floor(x + .5f); }
float cf_smooth(float x) {
    x = x < 0 ? 0 : x > 1 ? 1 : x;
    return x * x * (3 - 2 * x);
}

/* cos and sin of 0 to CF_ANGLE (60) degrees in 16 steps, 16.16 */
static const int cf_cos[17] = { 65536, 65396, 64975, 64277, 63303, 62058, 60547, 58777, 56756,
                                54491, 51993, 49273, 46341, 43211, 39896, 36410, 32768 };
static const int cf_sin[17] = { 0,     4286,  8554,  12785, 16962, 21066, 25080, 28986, 32768,
                                36410, 39896, 43211, 46341, 49273, 51993, 54491, 56756 };
/* the same of 0 to 90 degrees, for a theme's other tilt */
static const int cf_cos90[17] = { 65536, 65220, 64277, 62714, 60547, 57798, 54491, 50660, 46341,
                                  41576, 36410, 30893, 25080, 19024, 12785, 6424,  0 };
static const int cf_sin90[17] = { 0,     6424,  12785, 19024, 25080, 30893, 36410, 41576, 46341,
                                  50660, 54491, 57798, 60547, 62714, 64277, 65220, 65536 };

/* A right-hand cover t (16.16, >= 0) from the visual position: centre xc and depth zc (1/16 px),
 * rotation c, s (16.16), brightness (of 256) and the eye it is seen from (1/16 px). Left covers
 * mirror it. */
typedef struct {
    int xc, zc, c, s, bright, eye;
} pose_t;

static void pose(int t, pose_t *p) {
    int a = t < CF_ONE ? t : CF_ONE, other = cfs.tilt != CF_ANGLE;
    if (other) a = a * cfs.tilt / 90;
    const int *cs = other ? cf_cos90 : cf_cos, *sn = other ? cf_sin90 : cf_sin;
    int i = a >> 12, f = a & 4095;
    p->eye = CF_EYE16;
    p->c = cs[i] + (i < 16 ? (cs[i + 1] - cs[i]) * f >> 12 : 0);
    p->s = sn[i] + (i < 16 ? (sn[i + 1] - sn[i]) * f >> 12 : 0);
    if (t < CF_ONE) {
        /* xc = A t + B t^2: CF_X1 at t = 1, arriving at the stack's CF_XS slope, and quick at
         * first, so the covers turning in and out mid-step keep clear of each other. */
        int sq = (int)((unsigned)t * (unsigned)t >> 16);
        p->xc = (16 * (2 * cfs.x1 - cfs.xs) * t + 16 * (cfs.xs - cfs.x1) * sq) >> 16;
        p->zc = 16 * cfs.z1 * t >> 16;
        if (cfs.swing) {
            /* Depth follows the turn, less a small swell of lift px mid-way. The near edge's screen
             * x is two parabolas meeting flat at 45% of the way, 3 px past its rest place, and xc
             * follows from it, so the far edge goes on swinging out with no corner in the path.
             * t = 1 lands on the rest pose below. */
            p->zc = 16 * cfs.z1 * (p->s >> 4) / (cf_s1 >> 4) - (16 * cfs.lift * (t - sq) >> 16);
            int den = CF_EYE16 + p->zc - (CF_HALF16 * p->s >> 16);
            int peak = cf_rest16 + 48, rising = t < CF_SWING_T;
            int q =
                ((rising ? CF_SWING_T - t : t - CF_SWING_T) << 12) /
                (rising ? CF_SWING_T : CF_ONE - CF_SWING_T); /* 0 at the peak, 4096 at each end */
            int near = peak - ((rising ? peak - CF_HALF16 : 48) * (q * q >> 12) >> 12);
            p->xc = near * den / CF_EYE16 - (CF_HALF16 * p->c >> 16);
            /* The eye draws back mid-turn, by eye_turn (1 - t^4), so the far edge is less steep.
             * Depth goes with it, which keeps the near edge's place and height; at t = 0 and 1 no
             * pixel moves. */
            p->eye += 16 * cfs.eye_turn * (CF_ONE - (int)((unsigned)sq * (unsigned)sq >> 16)) >> 16;
            p->zc = p->eye * den / CF_EYE16 - p->eye + (CF_HALF16 * p->s >> 16);
        }
        p->bright = 256 - ((256 - cfs.bright1) * t >> 16);
    } else {
        int e = t - CF_ONE;
        p->xc = 16 * cfs.x1 + (16 * cfs.xs * e >> 16);
        p->zc = 16 * cfs.z1 + (16 * cfs.zs * e >> 16);
        if (e < CF_ONE)
            p->bright = cfs.bright1 - ((cfs.bright1 - cfs.bright2) * e >> 16);
        else if (cfs.third) /* whole to the third, gone half a step on: the ring's reach ends there
                             */
            p->bright = e < 2 * CF_ONE       ? cfs.bright2
                        : e < 5 * CF_ONE / 2 ? cfs.bright2 * (5 * CF_ONE / 2 - e) >> 15
                                             : 0;
        else
            p->bright = e < 2 * CF_ONE ? cfs.bright2 * (2 * CF_ONE - e) >> 16 : 0;
    }
}

/* Screen x (1/16 px right of the centre line) of the cover point u (1/16 px from its middle). */
static int project(const pose_t *p, int u) {
    return (p->xc + (u * p->c >> 16)) * p->eye / (p->eye + p->zc - (u * p->s >> 16));
}
#ifdef PEQ_HOST
/* For test/coverflow.py: the follower's state, and a lead (< 0: none) to render at. */
void cf_thumb(float *out) {
    out[0] = th.pos, out[1] = th.vel, out[2] = th.goal, out[3] = th.lead, out[4] = th.rate;
    out[5] = (float)th.on;
}
void cf_thumb_lead(float lead) { th.on = lead >= 0, th.lead = lead >= 0 ? lead : 0; }
/* For test/coverflow.py: an edge's path, finer than a frame's pixels. */
int cf_path_x(int t, int u) {
    pose_t p;
    pose(t, &p);
    return project(&p, u);
}
#endif

/* The cover point under screen x dx (1/16 px right of the centre line), the inverse of project;
 * 0 when the column misses the cover. *h gets the cover's projected height there (1/256 px). */
static int unproject(const pose_t *p, int dx, int *u, int *h) {
    int den = (p->eye * p->c + dx * p->s) >> 16;
    if (den <= 0) return 0;
    *u = (dx * (p->eye + p->zc) - p->eye * p->xc) / den;
    if (*u < -CF_HALF16 || *u >= CF_HALF16) return 0;
    *h = ART_SIZE * 256 * p->eye / (p->eye + p->zc - (*u * p->s >> 16));
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
    int top = CF_TOP256(h), bottom = top + h, step = (ART_SIZE << 20) / (h >> 4);
    int ins = h * inset / (ART_SIZE * 256), clipped_top = top + ins, clipped_bottom = bottom - ins;
    int solid_top = f->solid_top[col], solid_bottom = f->solid_bottom[col];
    int y = clipped_top >> 8, end = (clipped_bottom + 255) >> 8,
        last = end < cfs.view_h ? end : cfs.view_h;
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
    int reflect = h * cfs.reflect / ART_SIZE, stop = (bottom + reflect + 255) >> 8;
    int fade = (256 << 16) / reflect; /* fading per 1/256 px, 16.16 */
    unsigned dim = (unsigned)bright * cfs.reflect_top >> 8;
    wash = shade(cf_paper, 256u - dim);
    if (stop > cfs.view_h) stop = cfs.view_h;
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
    s0 = s0 < 0 ? 0 : s0, s1 = s1 > cfs.view_h ? cfs.view_h : s1;
    if (s1 <= s0) return;
    if (solid_bottom > solid_top && s0 <= solid_bottom && s1 >= solid_top) {
        s0 = s0 < solid_top ? s0 : solid_top;
        s1 = s1 > solid_bottom ? s1 : solid_bottom;
    } else if (s1 - s0 <= solid_bottom - solid_top)
        return;
    f->solid_top[col] = (short)s0, f->solid_bottom[col] = (short)s1;
}

/* The cover t (16.16, signed) turns from the visual position, under what is drawn. */
static unsigned short cf_inset[ART_SIZE] __attribute__((section(".scratch"))); /* 1/256 texel, symmetric */

/* Copy the theme's Cover Flow values to cfs. */
static void cf_restyle(void) {
    pose_t rest;
    cfs = theme()->cf;
    pose(CF_ONE, &rest);
    cf_s1 = rest.s, cf_rest16 = project(&rest, CF_HALF16);
}
/* cf_inset follows the theme's radius, or none under square art. */
static void cf_corners(void) {
    static short filled __attribute__((section(".scratch")));
    int radius = ipod_square_art() ? 0 : cfs.radius;
    if (radius == filled) return;
    filled = (short)radius;
    int r = ART_SIZE * radius / 166;
    memset(cf_inset, 0, sizeof cf_inset);
    for (int k = 0; k < r; ++k)
        cf_inset[k] = cf_inset[ART_SIZE - 1 - k] = (unsigned short)(arc_out(r, k) * 16);
}

/* One mapping of the art across a cover, at rest (draw_cover) and in the row (draw_quad): the texel
 * (0 at the far edge) at x of n from the far edge, the near half q (of 256) times as wide as the
 * far half; 256 spaces them evenly. Unsigned, x q stays under 2^32 / ART_SIZE. */
static int cf_texel(int x, int n, int q) {
    unsigned k = (unsigned)x * (unsigned)q * ART_SIZE / (unsigned)((n - x) * 256 + x * q);
    return k >= ART_SIZE ? ART_SIZE - 1 : (int)k;
}
/* A cover's q, near and far heights hn and hf: on the screen (per hf), r, from 1 at
 * coverflow.shelf.art_persp 0 to the plane's own hn / hf at 100; over the plane's own spacing
 * (per hn), r / (hn / hf), so 100 leaves it exactly as it is. */
static int cf_art_q(int hn, int hf, int per) {
    return ((hf + (hn - hf) * cfs.art_persp / 100) << 8) / per;
}

static void draw_cover(shelf_t *f, int t, const unsigned *tex) {
    pose_t p;
    int side = t < 0 ? -1 : 1;
    pose(t * side, &p);
    if (p.bright <= 0 || !tex) return;
    int a = project(&p, -CF_HALF16), b = project(&p, CF_HALF16);
    int lo = (16 * CF_CX + (a < b ? a : b)) >> 4, hi = (16 * CF_CX + (a < b ? b : a)) >> 4;
    /* the edges' depths: far over near is the near edge's height over the far edge's */
    int dn = p.eye + p.zc - (CF_HALF16 * p.s >> 16), df = dn + 2 * (CF_HALF16 * p.s >> 16);
    int q = cf_art_q(df, dn, df);
    for (int v = lo; v <= hi; ++v) { /* v: the column as if the cover were on the right */
        int col = side > 0 ? v : 2 * CF_CX - 1 - v, u, h;
        if (col < 0 || col >= CF_VIEW_W || !unproject(&p, (2 * v + 1 - 2 * CF_CX) * 8, &u, &h)) continue;
        int k = cf_texel(u + CF_HALF16, 2 * CF_HALF16, q);
        column(f, col, tex + (side > 0 ? k : ART_SIZE - 1 - k), h, p.bright, cf_inset[k]);
    }
}

/* A cover whose near and far edges have left its pose, between xl and xr (1/16 px right of the
 * centre line), its height hl to hr (1/256 px) straight across; the shorter edge is the far one. */
static void draw_quad(shelf_t *f, int xl, int xr, int hl, int hr, const unsigned *tex, int bright) {
    int lo = (16 * CF_CX + xl) >> 4, hi = (16 * CF_CX + xr) >> 4;
    int left = hr < hl, hf = left ? hr : hl, q = hf > 0 ? cf_art_q(hl + hr - hf, hf, hf) : 256;
    lo = lo < 0 ? 0 : lo, hi = hi >= CF_VIEW_W ? CF_VIEW_W - 1 : hi;
    for (int col = lo; xr > xl && col <= hi; ++col) {
        int dx = (2 * col + 1 - 2 * CF_CX) * 8 - xl;
        if (dx < 0 || dx >= xr - xl) continue;
        float p = (float)dx / (float)(xr - xl);
        int k = cf_texel(left ? xr - xl - dx : dx, xr - xl, q);
        k = left ? ART_SIZE - 1 - k : k;
        column(f, col, tex + k, (int)((float)hl + (float)(hr - hl) * p), bright, cf_inset[k]);
    }
}

/* The shelf with its facing point lead albums on from frac: each cover's t is its distance from
 * that point, on that side, and the whole shelf slides lead * row_shift px. Past row_from the row
 * forms: shrunk, no receding past row_flat from the focus, only spacing, each cover away from the
 * focus thinner and with less keystone. At lead 0 this is coverflow_render's own path, which
 * draws then. ring[j]: the cover j - CF_REACH from frac's centre, under CF_WIDE, moved on by
 * round(lead) albums, so the window goes round the facing cover (coverflow_paint). */
static void draw_row(shelf_t *f, int frac, const unsigned *const *ring) {
    float lead = th.lead, ld = lead < 0 ? -lead : lead, at = (float)frac / CF_ONE + lead;
    int moved = cf_round(lead);
    float shift = lead * cfs.row_shift, lim = 3.5f + (shift < 0 ? -shift : shift) / cfs.row_reach;
    float b = cf_smooth((ld * 100 - cfs.row_from) / (cfs.row_full - cfs.row_from));
    float sh = 1 - b * cfs.row_shrink / 100, flat = cfs.row_flat / 100.f;
    int focus = cf_round(at), after = at < (float)focus;
    for (int k = 0; k < CF_WIDE; ++k)
        for (int i = 0; i < (k ? 2 : 1); ++i) {
            int a = (i == 0) == after ? focus - k : focus + k, j = a - moved;
            float d = (float)a - at, t = d < 0 ? -d : d;
            if (j < -CF_WIDE_REACH || j > CF_WIDE_REACH || t > lim || !ring[j + CF_REACH]) continue;
            float tz = t > flat ? t - (t - flat) * b : t, e = b * cf_smooth(t * 100 / cfs.row_edge);
            pose_t p;
            pose((int)(tz * CF_ONE), &p);
            int near = project(&p, CF_HALF16), inner = project(&p, -CF_HALF16);
            float hn = ART_SIZE * 256.f * p.eye / (p.eye + p.zc - (CF_HALF16 * p.s >> 16));
            float hi = ART_SIZE * 256.f * p.eye / (p.eye + p.zc + (CF_HALF16 * p.s >> 16));
            float out = near + (t - tz) * cfs.row_out * 16;
            float width = (near - inner) * (1 - e * cfs.row_width / 100);
            float hf = (hn + (hi - hn) * (1 - e * cfs.row_key / 100)) * sh, right = d >= 0;
            hn *= sh;
            float xl = (right ? out - width : -out) + shift * 16;
            float xr = (right ? out : width - out) + shift * 16;
            float fade = (lim - t) * 2;
            int bright = tz < 2 ? p.bright : cfs.bright2;
            if (fade < 1) bright = (int)((float)bright * fade);
            if (bright > 0)
                draw_quad(f, (int)(xl * sh), (int)(xr * sh), (int)(right ? hf : hn),
                          (int)(right ? hn : hf), ring[j + CF_REACH], bright);
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
    if (!cfs.eye) cf_restyle(); /* once, for callers which render without opening the page */
    cf_corners();
    cf_paper = ipod_light() ? scheme()->paper : 0;
    shelf_t f;
    f.d = d, f.pitch = pitch;
    for (int x = 0; x < CF_VIEW_W; ++x) f.solid_top[x] = f.solid_bottom[x] = 0;
    for (int y = 0; y < cfs.view_h; ++y) memset(d + y * pitch, 0, CF_VIEW_W * 4);
    if (th.on && th.lead)
        draw_row(&f, frac, ring);
    else
        for (int k = 0; k <= CF_REACH; ++k)
            for (int i = 0; i < (k ? 2 : 1); ++i) {
                int j = slot(frac, k, i);
                draw_cover(&f, (j - CF_REACH) * CF_ONE - frac, ring[j]);
            }
    for (int y = 0; y < cfs.view_h;
         ++y) { /* over black; a count-down loop, which -Oz keeps tight */
        unsigned *p = d + y * pitch;
        if (cf_paper) { /* over Light Mode's paper, as the page's black flips to it */
            for (int x = 0; x < CF_VIEW_W; ++x) put(p + x, cf_paper, 256);
            continue;
        }
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
            int ins = h * cf_inset[(u + CF_HALF16) >> 4] / (ART_SIZE * 256), top = CF_TOP256(h);
            if ((y << 8) + 128 >= top + ins && (y << 8) + 128 < top + h - ins) /* column()'s corners */
                return j - CF_REACH;
        }
    return CF_MISS;
}

/* Covers decoded either side: more under a thumb theme, whose row shows about 11. */
static int fx_reach(void) { return cfs.thumb ? CF_WIDE_REACH : CF_REACH; }
#define FX_SLOTS (2 * fx_reach() + 1)
cf_depth_t fx __attribute__((section(".scratch")));


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
        unsigned paper = ipod_light() ? scheme()->paper & 255 : 0;
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
    unsigned *placeholder = fx.tex + FX_SLOTS * CF_TEXELS;
    if (!decode(PLACEHOLDER, placeholder, 0)) /* a theme image: the manager keeps it */
        for (int i = 0; i < CF_TEXELS; ++i) placeholder[i] = 0xff3a3a3au;
    fx.light = ipod_light();
}
/* A w x h RGBA8888 bitmap the renderers fill whole, so flagged opaque; 0 when out of memory. */
static void *opaque_frame(int w, int h) {
    void *b = bitmap_create_ex(w, h, w * 4, 1 /* RGBA8888 */);
    if (b) *(unsigned short *)((char *)b + 0xc) |= 1; /* BITMAP_FLAG_OPAQUE */
    return b;
}

HOT unsigned tilt_mix(unsigned a, unsigned b, unsigned w) { /* a to b, w of 256 */
    return shade(a, 256 - w) + shade(b, w);
}

/* The frame and every texture at once, or neither: the flat covers then stand in. */
static int fx_open(void) {
    fx.frame = opaque_frame(CF_VIEW_W, cfs.view_h);
    fx.tex = fx.frame ? calloc((unsigned)FX_SLOTS + 1, CF_TEXELS * 4) : 0;
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
    return fx.tex + FX_SLOTS * CF_TEXELS;
}

/* The FX_SLOTS covers around album c of n stay decoded, others are dropped as movement crosses
 * albums; ring gets each ring slot's texture: ring[j] the cover j - CF_REACH from the centre, so a
 * thumb theme's goes either side of the seven (the callers' wide). A small library repeats albums
 * round the ring, as the slide_menu wraps. Nearest the centre first; while the thumb holds the
 * covers, FX_DECODES a frame, and at rest the seven a shelf shows at once: the rest show the
 * placeholder until a later frame (fx.todo) decodes them. ponytail: still on the UI thread, two a
 * frame; a decoder thread if a letter's re-centring shows its fill-in. */
#define FX_DECODES 2
static void fx_window(int c, int n, const unsigned **ring) {
    int want[CF_WIDE], r = fx_reach(), slots = FX_SLOTS, budget = FX_DECODES;
    for (int j = 0; j < slots; ++j) want[j] = ((c + j - r) % n + n) % n + 1;
    for (int i = 0; i < slots; ++i) {
        int keep = 0;
        for (int j = 0; j < slots; ++j) keep |= fx.album[i] == want[j];
        if (!keep) fx.album[i] = 0;
    }
    fx.todo = 0;
    for (int k = 0; k < slots; ++k) {
        int d = (k + 1) / 2, j = k & 1 ? r + d : r - d, i = 0;
        while (i < slots && fx.album[i] != want[j]) ++i;
        if (i == slots) { /* at most slots albums are wanted, so a slot is free */
            for (i = 0; fx.album[i]; ++i) {}
            fx.album[i] = want[j];
            fx.art[i] = 0;
        }
        if (!fx.art[i] && ((!th.on && d <= CF_REACH) || budget-- > 0)) {
            fx.art[i] = fx_load(want[j] - 1, fx.tex + i * CF_TEXELS);
            fx.drawn = 0;
        }
        fx.todo |= !fx.art[i];
        ring[j + CF_REACH - r] = fx.art[i] ? fx.art[i] : fx.tex + FX_SLOTS * CF_TEXELS;
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
int coverflow_slide(void *w) { return w && w == cf.slide; }

static int retitle(const void *unused);

/* The covers round album c of n for this frame, in Light Mode's colours. */
static void fx_ring(int c, int n, const unsigned **ring) {
    if (fx.light != ipod_light()) {
        fx_placeholder();
        memset(fx.album, 0, sizeof fx.album); /* decoded art may have transparent corners too */
        fx.drawn = 0;
    }
    fx_window(c, n, ring);
    if (coverflow_bounded(cf.slide)) /* no wrap: only the artist's real neighbours, never repeats */
        for (int j = -fx_reach(); j <= fx_reach(); ++j)
            if ((unsigned)(c + j) >= (unsigned)n) ring[j + CF_REACH] = 0;
}

/* The flip (docs/ipod.md#cover-flow), under Classic's painter: Centre on a cover turns it
 * about the vertical axis on the centre line, right edge toward the viewer, and it grows into the
 * tracks panel on its back, the side covers parting; Return runs the same frames backwards. The
 * shelf's frame, rendered once without the centre cover, holds the side covers. fl.frame and
 * fl.back live only while a turn runs (about 776 KB): at rest the panel stands over the shelf's
 * frame, parted, drawn as it is. */
#define FLIP_MS 270
#define FLIP_TICK_MS 16
#define FLIP_H 294     /* the frame's rows, the client area's */
#define FLIP_EYE 700   /* px; the shelf's eye can be nearer than the panel's half width */
#define FLIP_EYE_Y 147 /* the row the eye looks along, the frame's middle */
#define FLIP_PART 24   /* px each way */
#define BACK_H 285
#define PANEL_Y 9
#define PANEL_HEAD 57
#define PANEL_MUTED 0xffaaaaaau /* stock's second line: the theme's muted, white when selected */
cf_flip_t fl __attribute__((section(".scratch")));

int coverflow_turning(void) { return fl.dir != 0; }

/* The time's share of the turn (16.16) at ms of FLIP_MS: ease-in-out, .42, 0, .58, 1, at each
 * sixteenth of the time. Its own mirror, so backwards it's the same frames. */
int coverflow_flip_eased(int ms) {
    static const int eased[17] = { 0,     497,   2039,  4686,  8465,  13339, 19187, 25776, 32768,
                                   39760, 46349, 52197, 57071, 60850, 63497, 65039, 65536 };
    if (ms <= 0) return 0;
    if (ms >= FLIP_MS) return CF_ONE;
    int at = ms * 65536 / FLIP_MS, i = at >> 12;
    return eased[i] + ((eased[i + 1] - eased[i]) * (at & 4095) >> 12);
}

/* The frame at e (16.16, the cover's 0 to the panel's CF_ONE) from tex: the side covers (CF_VIEW_W
 * x view_h), the cover, the back. e sets the turn (180 degrees), the parting and, linearly, the
 * quad's size and place. A flat BACK_W wide face seen from FLIP_EYE: the size shrinks x and y but
 * not depth, so u (-1 to 1 across the face, 12 bits) reaches 147 px towards the eye at any size.
 * Each screen column is one texture column, nearest, its slanted ends by row coverage. */
void coverflow_flip_render(unsigned *d, int pitch, int e, const unsigned *const tex[3]) {
    unsigned paper = (ipod_light() ? scheme()->paper : 0) | 0xff000000u;
    int part = FLIP_PART * e >> 16, right = CF_CX + part;
    for (int y = 0; y < FLIP_H; ++y) {
        unsigned *o = d + y * pitch;
        const unsigned *s = tex[0] + y * CF_VIEW_W;
        int shelf = y < cfs.view_h, from = shelf ? CF_CX - part : 0, to = shelf ? right : CF_VIEW_W;
        if (shelf) {
            memcpy(o, s + part, (unsigned)from * 4);
            memcpy(o + right, s + CF_CX, (unsigned)(CF_VIEW_W - right) * 4);
        }
        for (int x = from; x < to; ++x) o[x] = paper;
    }
    /* cos and sin of 180 e from the quarter's table, mirrored past 90 */
    int a = e <= CF_ONE / 2 ? e : CF_ONE - e, pos = a * 32, i = pos >> 16, f = (pos & 65535) >> 4;
    int c = cf_cos90[i] + (i < 16 ? (cf_cos90[i + 1] - cf_cos90[i]) * f >> 12 : 0);
    int sn = cf_sin90[i] + (i < 16 ? (cf_sin90[i + 1] - cf_sin90[i]) * f >> 12 : 0);
    int back = a != e, at = e >> 4; /* at: 12 bits */
    const unsigned *t = tex[back ? 2 : 1];
    if (!c || !t) return; /* edge on */
    int tw = back ? BACK_W : ART_SIZE, th = back ? BACK_H : ART_SIZE;
    int half = 1280 + (16 * (BACK_W / 2 - ART_SIZE / 2) * at >> 12);  /* half the width, 1/16 px */
    int top = 256 * cfs.top + (256 * (PANEL_Y - cfs.top) * at >> 12); /* 1/256 px */
    int bottom = top + 40960 + (256 * (BACK_H - ART_SIZE) * at >> 12), eye_y = 256 * FLIP_EYE_Y;
    int depth = sn * (BACK_W / 2) / FLIP_EYE, cw = half * (back ? -c : c) >> 12;
    for (int x = 0; x < CF_VIEW_W; ++x) {
        int dx = (2 * x + 1 - 2 * CF_CX) * 8, den = cw + (dx * depth >> 12);
        if (!den) continue;
        int u = dx * 65536 / den;
        if (u < -4096 || u >= 4096) continue;
        int k = (1 << 28) / (65536 - (depth * u >> 12)); /* the eye's scale there, 12 bits */
        int y0 = eye_y + ((top - eye_y) * k >> 12), y1 = eye_y + ((bottom - eye_y) * k >> 12);
        int h = y1 - y0;
        if (h < 16) continue;
        int col = (back ? 4095 - u : u + 4096) * tw >> 13, step = (th << 20) / (h >> 4);
        int ins = back ? 0 : h * cf_inset[col] / (ART_SIZE * 256), from = y0 + ins, to = y1 - ins;
        int first = from >> 8, last = (to - 1) >> 8, y = first < 0 ? 0 : first;
        unsigned *o = d + y * pitch + x;
        for (; y <= last && y < FLIP_H; ++y, o += pitch) {
            int v = (((y << 8) + 128 - y0) >> 4) * step >> 20; /* texel row */
            unsigned px = t[(v < 0 ? 0 : v >= th ? th - 1 : v) * tw + col] | 0xff000000u;
            if (y != first && y != last) {
                *o = px;
                continue;
            }
            int a0 = y << 8 > from ? y << 8 : from, a1 = (y + 1) << 8 < to ? (y + 1) << 8 : to;
            *o = tilt_mix(*o, px, (unsigned)(a1 - a0)) | 0xff000000u;
        }
    }
}

/* The side covers round cf.album, in the shelf's frame, which is drawn afresh back on the shelf;
 * the cover is the ring's middle: the no-art cover when it has no texture. */
static void flip_sides(void) {
    const unsigned *wide[CF_WIDE], **ring = wide + CF_WIDE_REACH - CF_REACH;
    fx_ring(cf.album, (int)widget_count_children(cf.slide), ring);
    fl.front = ring[CF_REACH];
    ring[CF_REACH] = 0;
    unsigned *d = (unsigned *)bitmap_lock_buffer_for_write(fx.frame);
    if (d) {
        coverflow_render(d, (int)(bitmap_get_line_length(fx.frame) / 4), 0, ring);
        bitmap_unlock_buffer(fx.frame);
    }
    fx.drawn = 0;
    fl.light = ipod_light();
}

/* White on the header in either half: the grey Light Mode turns white. */
static void panel_ink(void) {
    unsigned white = unflip(255) * 0x010101u;
    widget_set_prop_int(fl.title, "style:normal:text_color", (int)(0xff000000u | white));
    widget_set_prop_int(fl.artist, "style:normal:text_color", (int)(0xc0000000u | white));
}

/* The panel BACK_W wide from (x, y), h rows, its header head: before its rows (over 0) its paper
 * and the header's fill, after them (over 1) the header's shadow over the rows and a 1 px border
 * just outside it, flat and opaque, so its four sides are one colour whatever lies under them
 * (a translucent ring took Cover Flow's sides under it). Shared with navigation.c's hold menu. */
void coverflow_panel_paint(void *canvas, int x, int y, int head, int h, int over) {
    if (!over) {
        stock_canvas_set_fill_color(canvas, (ipod_light() ? scheme()->paper : 0) | 0xff000000u);
        canvas_fill_rect(canvas, x, y, BACK_W, h);
        for (int j = 0; j < head; ++j) {
            unsigned rgb = RGBA(mix(0x6d8db2, 0x8bacd0, j, head - 1)) & 0xffffff;
            stock_canvas_set_fill_color(canvas, rgb | 0xf0000000u);
            canvas_fill_rect(canvas, x, y + j, BACK_W, 1);
        }
        return;
    }
    static const unsigned char shadow[8] = { 0, 28, 54, 50, 41, 27, 14, 0 };
    for (int j = 0; j < 8; ++j) {
        stock_canvas_set_fill_color(canvas, (unsigned)shadow[j] << 24);
        canvas_fill_rect(canvas, x, y + head - 2 + j, BACK_W, 1);
    }
    stock_canvas_set_fill_color(canvas, RGBA(ipod_light() ? 0xb4b9c0 : 0x333333));
    canvas_fill_rect(canvas, x - 1, y - 1, BACK_W + 2, 1);
    canvas_fill_rect(canvas, x - 1, y + h, BACK_W + 2, 1);
    canvas_fill_rect(canvas, x - 1, y, 1, h);
    canvas_fill_rect(canvas, x + BACK_W, y, 1, h);
}

/* ringnav_paint over the panel's widgets; 1 when w was the frame's. */
static int flip_paint(void *w, void *canvas) {
    if (w == fl.sheet || w == fl.panel) /* the sheet first in the panel, the panel last */
        coverflow_panel_paint(canvas, 0, 0, PANEL_HEAD, BACK_H, w == fl.panel);
    if (w != cf.covers) return 0;
    if (fl.light != ipod_light()) {
        flip_sides();
        panel_ink();
        fl.drawn = -1;
    }
    if (!fl.frame) { /* at rest: the turn's last frame where the panel doesn't cover it */
        unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
        int h = cfs.view_h, side = CF_VIEW_W - CF_CX - FLIP_PART;
        int ls[4] = { FLIP_PART, 0, CF_CX - FLIP_PART, h }, ld[4] = { 0, 0, CF_CX - FLIP_PART, h };
        int rs[4] = { CF_CX, 0, side, h }, rd[4] = { CF_CX + FLIP_PART, 0, side, h };
        canvas_draw_image(canvas, fx.frame, ls, ld);
        canvas_draw_image(canvas, fx.frame, rs, rd);
        stock_canvas_set_fill_color(canvas, (ipod_light() ? scheme()->paper : 0) | 0xff000000u);
        canvas_fill_rect(canvas, CF_CX - FLIP_PART, 0, 2 * FLIP_PART, h);
        canvas_fill_rect(canvas, 0, h, CF_VIEW_W, FLIP_H - h);
        stock_canvas_set_fill_color(canvas, fill);
        return 1;
    }
    int ms = (int)((unsigned)time_now_ms() - fl.t0);
    int e = !fl.dir ? CF_ONE : coverflow_flip_eased(fl.dir > 0 ? ms : FLIP_MS - ms);
    if (e != fl.drawn) {
        unsigned *d = (unsigned *)bitmap_lock_buffer_for_write(fl.frame);
        const unsigned *tex[3] = { (const unsigned *)bitmap_lock_buffer_for_read(fx.frame),
                                   fl.front, fl.back };
        if (d && tex[0])
            coverflow_flip_render(d, (int)(bitmap_get_line_length(fl.frame) / 4), e, tex);
        if (tex[0]) bitmap_unlock_buffer(fx.frame);
        if (d) bitmap_unlock_buffer(fl.frame);
        fl.drawn = e;
    }
    int r[4] = { 0, 0, CF_VIEW_W, FLIP_H };
    canvas_draw_image(canvas, fl.frame, r, r);
    return 1;
}

#if Q2POD_DEV
/* --dev builds only (docs/internals.md#coverflow-step): each frame of a run in RAM, a store a
 * frame. They go to /tmp/q2-cf-gaps.log at the first paint after the run, if /tmp/q2-cf-gaps.on
 * exists. */
#define GAP_FRAMES 128
#define GAP_MAX_BYTES 262144
static struct {
    unsigned n, last;
    struct {
        unsigned short gap, decode, render;
        short offset;
    } row[GAP_FRAMES];
} gaps __attribute__((section(".scratch")));
static void gap_note(void *w, unsigned t0, unsigned t1, unsigned t2) {
    unsigned i = gaps.n++ % GAP_FRAMES;
    gaps.row[i].gap = (unsigned short)(t0 - gaps.last);
    gaps.row[i].decode = (unsigned short)(t1 - t0);
    gaps.row[i].render = (unsigned short)(t2 - t1);
    gaps.row[i].offset = (short)I(w, SLIDE_OFFSET);
    gaps.last = t0;
    if (P(w, SLIDE_ANIMATOR) || th.on) return;
    void *on = gaps.n > 1 ? fopen("/tmp/q2-cf-gaps.on", "rb") : 0;
    void *f = on ? fopen("/tmp/q2-cf-gaps.log", "ab") : 0;
    if (on) fclose(on);
    if (f && !fseek(f, 0, 2) && ftell(f) < GAP_MAX_BYTES) {
        char s[64];
        tk_snprintf(s, sizeof s, "== run at %u ms: %u frames\n", t0, gaps.n);
        fputs(s, f);
        for (i = gaps.n > GAP_FRAMES ? gaps.n - GAP_FRAMES : 0; i < gaps.n; ++i) {
            unsigned k = i % GAP_FRAMES;
            tk_snprintf(s, sizeof s, "gap=%u decode=%u render=%u offset=%d\n", gaps.row[k].gap,
                        gaps.row[k].decode, gaps.row[k].render, gaps.row[k].offset);
            fputs(s, f);
        }
    }
    if (f) fclose(f);
    gaps.n = 0;
}
#endif
/* ringnav_paint (the border hook, after stock painted the slide_menu's empty children) calls this
 * for every widget: over Coverflow's slide_menu it draws the frame, rendered again only when the
 * position or a texture has changed. */
void coverflow_paint(void *w, void *canvas) {
    int c, frac, n;
    if (fl.panel && w && flip_paint(w, canvas)) return;
    if (!w || w != cf.slide || !fx.frame || cf.screen != COVERS ||
        !visual(w, n = (int)widget_count_children(w), &c, &frac))
        return;
    int lead = (int)(th.lead * CF_ONE), sh = 0;
    if (th.on) { /* finer than the offset's whole px */
        int q = cf_round(th.pos);
        frac = (int)((th.pos - (float)q) * CF_ONE);
        c = ((I(w, SLIDE_INDEX) + q) % n + n) % n;
        sh = cf_round(th.lead); /* draw_row's: the window round the facing cover */
    }
    const unsigned *wide[CF_WIDE], **ring = wide + CF_WIDE_REACH - CF_REACH;
#if Q2POD_DEV
    unsigned gap_t0 = (unsigned)time_now_ms();
#endif
    fx_ring(c + sh, n, ring);
#if Q2POD_DEV
    unsigned gap_t1 = (unsigned)time_now_ms();
#endif
    if (!fx.drawn || c != fx.c || frac != fx.frac || lead != fx.lead) {
        unsigned *d = (unsigned *)bitmap_lock_buffer_for_write(fx.frame);
        if (!d) return;
        coverflow_render(d, (int)(bitmap_get_line_length(fx.frame) / 4), frac, ring);
        bitmap_unlock_buffer(fx.frame);
        fx.c = c, fx.frac = frac, fx.lead = lead, fx.drawn = 1;
    }
#if Q2POD_DEV
    gap_note(w, gap_t0, gap_t1, (unsigned)time_now_ms());
#endif
    if (!th.on && c + 1 != cf.titled && !cf.title_timer) cf.title_timer = timer_add(retitle, 0, 0);
    int r[4] = { 0, 0, CF_VIEW_W, cfs.view_h };
    canvas_draw_image(canvas, fx.frame, r, r);
}

/* Touch (docs/internals.md#coverflow-touch). The slide_menu stays insensitive: the covers' view
 * takes the touch and moves the offset itself, so stock's drag and throw never run. A drag follows
 * the finger, CF_DRAG_PX an album; the release glides on at the finger's speed, decelerating,
 * through the wheel's own animator (carousel_glide), to the cover that speed reaches; a tap brings
 * a side cover to the centre or opens the centre one. A touch while the covers move stops them.
 * The press is the covers' view's; the moves and the release are the page's, so a release off the
 * covers still ends the touch, and one never seen ends at the next key (coverflow_lost_touch). */
#define CF_SAMPLES 8

/* EASE_FOLLOW (docs/internals.md#coverflow-step): a step follows its goal, critically damped, so a
 * tick moves the goal and never the speed. Over s time constants from the live offset, d px short
 * of the goal with speed v, what is left is (d + (d - v tau) s) e^-s, and the animator's share is
 * 1 - (1 + a s) e^-s with a = 1 - v tau / d. Run by stock's animator, as a float easing. */
static struct {
    int d, tau;
    float a, span; /* span: the run's length, in time constants */
} ff __attribute__((section(".scratch")));

/* e^-s, s under 256: (e^(-s/256))^256, smooth and falling, about .1% over at s = 7. */
float ff_decay(float s) {
    float x = 1 - s / 256 + s * s / 131072;
    for (int i = 0; i < 8; ++i) x *= x;
    return x;
}
float coverflow_follow(float k) {
    if (k >= 1) return 1;
    float s = k * ff.span;
    return 1 - (1 + ff.a * s) * ff_decay(s);
}
/* A run d px long at time constant tau ms, picking up the speed elapsed ms into the last one (< 0:
 * from rest). Returns its ms: until within half a px of the goal, as stock's update truncates, so
 * the share's jump to 1 is one whole px at most. A goal right ahead of a cover too fast to stop for
 * it (a < 0) gives up the speed it can't use. */
int coverflow_follow_aim(int d, int tau, int elapsed) {
    float a = 1, e = 1, s = 0, size = (float)ABS(d);
    if (elapsed >= 0 && d) {
        s = (float)elapsed / (float)ff.tau;
        float v = (float)ff.d * (1 - ff.a + ff.a * s) * ff_decay(s) / (float)ff.tau;
        a = 1 - v * (float)tau / (float)d;
        if (a < 0) a = 0;
    }
    s = 0;
    do {
        s += .25f;
        e *= .7788008f; /* e^-1/4 */
    } while (size * (1 + a * s) * e > .5f);
    ff.d = d, ff.tau = tau, ff.a = a, ff.span = s;
    return (int)(s * (float)tau);
}
static struct {
    int down, drag, caught, x, y, from, n; /* from: the offset at the press, or the drag's start */
    int sx[CF_SAMPLES];
    unsigned st[CF_SAMPLES];
} tc __attribute__((section(".scratch")));

int stride_of(void *s) { return slide_menu_item_width(s) + I(s, SLIDE_SPACER); }
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

int covers_live(void) { return cf.slide && cf.screen == COVERS && !cf.timer; }

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
        if (!covers_live() || th.on) return 0; /* the thumb's covers settle first */
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

/* Classic's caption: both lines in its face, Now Playing's title's. */
static int caption_bold(void) { return theme()->font[ROLE_TITLE] == FONT_CLASSIC_LIGHT; }

/* The captions name album c (or a card's), once each time it changes. */
void titles(unsigned c) {
    unsigned n = widget_count_children(cf.slide);
    if ((int)c + 1 == cf.titled) return;
    cf.titled = (int)c + 1;
    void *r = c + CARDS < n ? deque_at(cf.albums, c) : (void *)0;
    widget_set_text_utf8(cf.name, r                            ? P(r, REC_ALBUM)
                                  : cf.scoped == ARTIST_ALBUMS ? "All albums"
                                                               : sort_names[cf.sort]);
    widget_set_text_utf8(cf.artist, r ? cover_artist(r) : ""); /* NULL leaves the old one */
    if (caption_bold()) {                                      /* by its text */
        theme_font(cf.name, ROLE_NP, FONT_ROW);
        theme_font(cf.artist, ROLE_NP, FONT_ROW);
    }
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
    if (cf.slide && cf.screen == COVERS && !th.on) titles((unsigned)fx.c);
    return 0;
}

/* One child per album plus the cards (CARDS), moved by the wheel and by touch on the covers' view
 * (touch); the slide_menu itself takes no touch. With depth it spans the frame, so every step
 * repaints all of it, and its cfs.view_h square items with a negative spacer move one album per
 * the theme's stride in px; the children stay empty under the frame. The flat fallback is the
 * stock images, 160 px, as before. ponytail: one child per album; if large libraries lag,
 * virtualize to a recycled window of children. */
static void covers(int dir) {
    cf.screen = COVERS;
    if (cf.text_albums) {
        unsigned n = deque_size(cf.albums);
        void *view = list("Albums", (int)n);
        for (unsigned i = 0; i < n; ++i)
            page_row_into(view, (int)i, P(deque_at(cf.albums, i), REC_ALBUM), pick);
        page_push(view, dir);
        return;
    }
    widget_set_visible(cf.body, 0, 0);
    if (!cf.covers) {
        void *f = widget_factory();
        int depth = fx_open();
        cf.covers = widget_factory_create_widget(f, "view", cf.page, 0, 0, 375, client_h());
        cf.slide = widget_factory_create_widget(f, "slide_menu", cf.covers, 0, depth ? 0 : 24, 375,
                                                depth ? cfs.view_h : ART_SIZE);
        if (depth) slide_menu_set_spacer(cf.slide, cfs.stride - cfs.view_h);
        widget_set_sensitive(cf.slide, 0);
        for (unsigned i = 0, n = deque_size(cf.albums); i < n + CARDS; ++i) {
            void *img = image_create(cf.slide, 0, 0, 0, 0);
            image_set_draw_type(img, 4); /* scale_auto, as the stock cover rows */
            if (!depth) image_base_set_image(img, PLACEHOLDER);
            widget_set_prop_int(img, "clickable", 1);
            widget_on(img, EVT_CLICK, pick, (void *)(long)i);
        }
        /* Album over artist under the frame, white and larger, then grey, clear of the rounded
         * glass (docs/ipod.md#coverflow). */
        cf.name = text(cf.covers, CF_EDGE, cfs.text_y, 375 - 2 * CF_EDGE, CF_NAME_H);
        widget_set_prop_int(cf.name, "style:normal:font_size", cfs.name_px);
        cf.artist =
            text(cf.covers, CF_EDGE, cfs.text_y + cfs.artist_y, 375 - 2 * CF_EDGE, CF_ARTIST_H);
        widget_set_prop_int(cf.artist, "style:normal:font_size", cfs.artist_px);
        widget_set_prop_int(cf.artist, "style:normal:text_color",
                            !caption_bold() ? (int)CF_GREY
                            : ipod_light()  ? (int)0xffffffffu /* the ink, as the album's */
                                            : (int)CF_GREY_BOLD);
        slide_menu_set_value(cf.slide, cf.album);
        widget_on(cf.slide, EVT_VALUE_CHANGED, changed, 0);
        tc.down = th.on = 0; /* a touch or thumb on the covers these replace */
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
    covers(-1); /* the text list's only way back here is from its tracks */
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
    page_retitle(cf.page, cf.title, progress);
    cf.timer = timer_add(poll, 0, 250);
    return 0;
}

/* A cold open's progress (docs/internals.md#watchdog): a line per phase, synced to /mnt/data and
 * removed when load() ends, so one left at boot names the phase a stalled open reached. Only an
 * open that queries the albums or finds albums without art writes it (cf_cold); a warm one, the
 * usual, writes nothing. A few lines per open: flash, never per album. */
#define CF_TRACE PEQ_ROOT "/mnt/data/q2-cf-trace"
static int cf_traced; /* this open is a cold one */
static void cf_trace(unsigned t0, const char *what, unsigned a, unsigned b) {
    if (!cf_traced) return;
    char line[96];
    int n = b ? snprintf(line, sizeof line, "%s%u/%u %u ms\n", what, a, b, time_now_ms() - t0)
              : snprintf(line, sizeof line, "%s%u %u ms\n", what, a, time_now_ms() - t0);
    void *f = fopen(CF_TRACE, "a");
    if (!f) return;
    fwrite(line, 1, n, f);
    fflush(f);
    fsync(fileno(f));
    fclose(f);
}
/* From here this open traces, from a fresh file's "start". */
static void cf_cold(unsigned t0) {
    if (cf_traced) return;
    cf_traced = 1;
    unlink(CF_TRACE);
    cf_trace(t0, "start albums=", cf.stock ? deque_size(cf.stock) : 0, 0);
}

/* On open: the albums, then art for the ones with no cache file (PictureFlow's first-launch build;
 * later opens resume). The albums are queried again only after the library changed (or Refresh
 * Coverflow): stock's sort converts both names to pinyin on every comparison. */
static void load_albums(unsigned t0);
static void load(void) {
    unsigned t0 = time_now_ms();
    cf_traced = 0;
    load_albums(t0);
    if (cf_traced) unlink(CF_TRACE);
}
static void load_albums(unsigned t0) {
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
    cf.body =
        widget_factory_create_widget(widget_factory(), "view", cf.page, 0, 0, 375, client_h());
    int n = 0;
    unsigned gen = library_gen;
    if (cf.albums_gen != gen && !cf.scoped) drop_albums();
    if (cf.scoped)
        n = (int)deque_size(cf.scoped == RATED_ALBUMS ? cf.stock : cf.albums);
    else if (cf.stock)
        n = (int)deque_size(cf.stock);
    else if (!library_scanning()) {
        cf_cold(t0);
        cf.stock = staged(albums, 0, &n);
        cf_trace(t0, "staged albums=", n, 0);
        if (n > 0) {
            void *v = split(cf.stock);
            cf_trace(t0, "split albums=", deque_size(v), 0);
            cf.stock = music_albums(v);
            cf_trace(t0, "music_albums albums=", deque_size(cf.stock), 0);
        }
        cf.albums_gen = gen;
        drop_unknown();
        cf_trace(t0, "drop_unknown albums=", deque_size(cf.stock), 0);
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
        cf_trace(t0, "sorted albums=", deque_size(cf.albums), 0);
    }
    if (n <= 0) {
        drop_albums(); /* only a real list is kept */
        cf.screen = COVERS; /* Return goes Home */
        list("Update Local Music first", 0);
        return;
    }
    unsigned count = deque_size(cf.albums);
    if (cf.text_albums) {
        covers(0);
        return; /* Text lists need no cover cache or decoding. */
    }
    char path[512];
    if (!cf.saved_album) BLOB_IO(LAST_ALBUM, cf.saved_album, 0); /* outlives a reboot */
    cf.jobs = calloc(count, sizeof(job_t));
    unsigned char *shared = folders_shared(cf.albums, count);
    for (unsigned i = 0; i < count; ++i) {
        if (i && !(i % 1000)) cf_trace(t0, "jobs ", i, count);
        void *r = deque_at(cf.albums, i);
        unsigned key = album_key(r);
        if (key == cf.saved_album && !on_sort && !cf.scoped) cf.album = (int)i;
        job_t *j = cf.jobs ? &cf.jobs[cf.total] : 0;
        if (j && P(r, REC_PATH) && access(art_path(path, key, ""), 0) && (j->track = strdup(P(r, REC_PATH)))) {
            cf_cold(t0);
            j->key = key;
            j->shared = shared ? shared[i] : 1; /* no memory: no cover.jpg that may be another's */
            ++cf.total;
        }
    }
    free(shared);
    cf_trace(t0, "done jobs=", cf.total, count);
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
        page_retitle(cf.page, cf.title, "Storage unavailable");
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

/* A label's face at px, in every state the labels style: bold, the theme's (bold itself), by its
 * text as theme_font has it; else stock's regular, as the theme's rows' second lines. */
void panel_font(void *label, int px, int bold) { /* shared with navigation.c */
    static const char *const names[] = { "style:normal:font_name", "style:focused:font_name",
                                         "style:disable:font_name" },
                             *const sizes[] = { "style:normal:font_size", "style:focused:font_size",
                                                "style:disable:font_size" };
    const char *face = bold ? theme_face(widget_get_text(label), -1, &px) : (const char *)0;
    for (unsigned i = 0; i < sizeof names / sizeof *names; ++i) {
        widget_set_prop_str(label, names[i], face ? face : "default");
        widget_set_prop_int(label, sizes[i], px);
    }
}

static void *panel_label(void *parent, int x, int y, int w, int h, const char *s, int px,
                         int bold) {
    void *label = text(parent, x, y, w, h);
    widget_use_style(label, "s_scrlabel_white20l");
    widget_set_text_utf8(label, s);
    panel_font(label, px, bold);
    return label;
}

/* A row's length as stock's clock writes it, blank until it's known (or a file nothing could read).
 */
static void panel_length(void *label, void *track) {
    int sec = song_seconds(track);
    char t[16] = "";
    if (sec > 0) toolsTimeItoa(t, sec);
    widget_set_text_utf8(label, t);
}

/* The panel as it stands into fl.back, hidden after: painted by stock on an offline canvas of the
 * screen's size and format, at its screen place, as stock's window snapshots are, so the paint
 * hooks' coordinates hold. The screen's lcd is BGRA8888; the frame's RGBA. No canvas, or a layout
 * not the screen's: the paper, so the turn still runs. */
static void flip_capture(void) {
    void *c = canvas_offline_create(CF_VIEW_W, 320, LCD_BGRA8888);
    const unsigned *fb = 0;
    if (c) {
        canvas_offline_begin_draw(c);
        int sy = I(cf.page, W_Y);
        canvas_translate(c, I(cf.page, W_X), sy);
        widget_set_visible(fl.panel, 1, 0);
        widget_layout(fl.panel);
        widget_paint(fl.panel, c);
        canvas_offline_end_draw(c);
        void *lcd = P(c, CANVAS_LCD);
        if (I(lcd, LCD_W) == CF_VIEW_W && I(lcd, LCD_H) == 320 &&
            I(lcd, LCD_LINE_LENGTH) == CF_VIEW_W * 4 && I(lcd, LCD_FORMAT) == LCD_BGRA8888 &&
            sy + PANEL_Y + BACK_H <= 320)
            fb = (const unsigned *)P(lcd, LCD_OFFLINE_FB);
        if (fb) fb += (sy + PANEL_Y) * CF_VIEW_W + PANEL_X + I(cf.page, W_X);
    }
    unsigned *o = fl.back, paper = (ipod_light() ? scheme()->paper : 0) | 0xff000000u;
    for (int y = 0; y < BACK_H; ++y)
        for (int x = 0; x < BACK_W; ++x) {
            unsigned p = fb ? fb[y * CF_VIEW_W + x] : paper;
            *o++ = 0xff000000u | (p & 0xff00) | (p & 255) << 16 | (p >> 16 & 255);
        }
    if (c) canvas_offline_destroy(c);
    widget_set_visible(fl.panel, 0, 0);
}

static void flip_done(void);
static int flip_tick(const void *unused);
static void flip_start(int dir) {
    fl.dir = dir, fl.drawn = -1;
    fl.t0 = (unsigned)time_now_ms();
    if (!(fl.timer = timer_add(flip_tick, 0, FLIP_TICK_MS))) flip_done();
    widget_invalidate_force(cf.covers, 0);
}

static int flip_tick(const void *unused) {
    if ((unsigned)time_now_ms() - fl.t0 < FLIP_MS) {
        widget_invalidate_force(cf.covers, 0);
        return 8; /* RET_REPEAT */
    }
    fl.timer = 0;
    flip_done();
    return 0;
}

/* The turn's buffers go, as each turn ends. */
static void flip_drop(void) {
    if (fl.frame) bitmap_destroy(fl.frame);
    free(fl.back);
    fl.frame = 0, fl.back = 0;
}

/* A turn's buffers, both or neither. */
static int flip_alloc(void) {
    fl.frame = opaque_frame(CF_VIEW_W, FLIP_H);
    fl.back = fl.frame ? calloc(BACK_W * BACK_H, 4) : 0;
    if (!fl.back) flip_drop();
    return fl.back != 0;
}

/* Everything goes; the widgets are the page's, or flip_done's. */
static void flip_free(void) {
    stop_timer(&fl.timer);
    flip_drop();
    memset(&fl, 0, sizeof fl);
}

/* Captions and the wheel's surface: off for the panel, on again for the shelf. */
static void flip_shelf(int on) {
    widget_set_visible(cf.slide, on, 0);
    widget_set_visible(cf.name, on, 0);
    widget_set_visible(cf.artist, on, 0);
}

/* The album's panel and its first turn, or 0: not this theme or page, or no room, so the text list.
 * Nothing starts until the panel is built and painted: a slow album waits at the cover. Stock's
 * hit test skips insensitive widgets, and the wheel a hidden slide_menu. */
static int flip_open(void *r, int n) {
    if (fl.panel) return 1; /* Centre before flip_close's timer: the panel stands */
    if (!classic_pages() || !cf.covers || !fx.frame || !flip_alloc()) return 0;
    void *f = widget_factory();
    fl.panel = widget_factory_create_widget(f, "view", cf.page, PANEL_X, PANEL_Y, BACK_W, BACK_H);
    fl.sheet = widget_factory_create_widget(f, "view", fl.panel, 0, 0, BACK_W, BACK_H);
    /* Font sizes are AWTK's, a face's whole height: 1.43 of its em in the theme's face. */
    int row = theme()->page_row;
    void *view = rows_view(fl.panel, PANEL_HEAD, BACK_W, BACK_H - PANEL_HEAD, n, row);
    char name[512];
    for (int i = 0; i < n; ++i) {
        void *t = deque_at(cf.tracks, (unsigned)i);
        void *item = list_item_create(view, 0, i * row, BACK_W, row);
        widget_use_style(item, "s_listitem_black");
        panel_label(item, 12, 0, 204, row, track_name(name, sizeof name, t), 25, 1);
        void *length = panel_label(item, 222, 0, 60, row, "", 23, 1);
        widget_set_prop_str(length, "style:normal:text_align_h", "right");
        widget_set_prop_int(length, "style:normal:text_color", (int)PANEL_MUTED);
        panel_length(length, t);
        widget_on(item, EVT_CLICK, play, (void *)(long)i);
    }
    /* cf.title: where a row's "Storage unavailable" goes */
    cf.title = fl.title = panel_label(fl.panel, 12, 1, 270, 33, album_name(r), 31, 1);
    fl.artist = panel_label(fl.panel, 12, 30, 270, 20, cover_artist(r), 18, 0);
    panel_ink();
    widget_set_sensitive(cf.covers, 0);
    flip_shelf(0);
    flip_sides();
    flip_capture();
    flip_start(1);
    return 1;
}

/* Return on the panel, once a click's handling is over: its selection and scroll as they stand. */
static int flip_close(const void *unused) {
    cf.timer = 0;
    if (!flip_alloc()) { /* no room to turn back: straight to the covers */
        flip_done();
        return 0;
    }
    flip_capture();
    flip_start(-1);
    return 0;
}

static void flip_done(void) {
    int to_panel = fl.dir > 0;
    fl.dir = 0;
    if (to_panel) {
        flip_drop();
        widget_set_visible(fl.panel, 1, 0);
        widget_invalidate_force(cf.page, 0);
        return;
    }
    widget_destroy(fl.panel);
    flip_free();
    cf.title = 0;
    flip_shelf(1);
    to_covers(0);
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
    if (flip_open(r, n)) return 0;
    if (cf.covers) { /* none in the text list; stock's hit test skips insensitive, not hidden,
                      * widgets, and the covers' view over the list would take its taps and swipes */
        widget_set_visible(cf.covers, 0, 0);
        widget_set_sensitive(cf.covers, 0);
    }
    void *view = list(P(r, REC_ALBUM), n);
    /* the album over its tracks, all centred, but under a theme's own rows */
    int centred = theme()->page_title_h;
    if (centred) widget_set_prop_int(cf.title, "style:normal:font_size", cfs.name_px);
    for (int i = 0; i < n; ++i) {
        void *row = page_row_detail(
            view, i, track_name(name, sizeof name, deque_at(cf.tracks, (unsigned)i)), 0, play);
        if (centred) widget_use_style(widget_get_child(row, 0), "s_scrlabel_white20c");
    }
    page_push(view, cf.text_albums && cf.scoped == ARTIST_ALBUMS); /* from a list, not the covers */
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
 * again, both at Coverflow's next open (a page open under Settings keeps what it shows). Stock's
 * Library covers (.sldp, made once per album name, which only a settings reset clears) go too,
 * and AWTK's cached bitmaps with them, as the cover thread writes them again at the same names as
 * rows show. The library generation's step is a songtable write's, which Most Played reads
 * again too. */
void coverflow_refresh_art(void) {
    static const char *const dirs[] = { ART_DIR, PEQ_ROOT "/mnt/mmc/.sldp" };
    for (unsigned i = 0; i < 2; ++i) {
        void *dir = opendir(dirs[i]);
        for (struct dirent *e; dir && (e = readdir(dir));) {
            char path[600];
            snprintf(path, sizeof(path), "%s/%s", dirs[i], e->d_name);
            unlink(path); /* "." and ".." fail harmlessly */
        }
        if (dir) closedir(dir);
    }
    image_manager_unload_all(image_manager());
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
    if (fl.panel) { /* the panel's way back; during a turn, nothing */
        if (!fl.dir) rearm(&cf.timer, flip_close, 0);
        return 11; /* RET_STOP */
    }
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
    th.on = 0; /* the poll kept the album named (the page may be part-destroyed here) */
    if (cf.saved_album) BLOB_IO(LAST_ALBUM, cf.saved_album, 1);
    flip_free();
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
    cf_restyle();
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
    cf.body =
        widget_factory_create_widget(widget_factory(), "view", cf.page, 0, 0, 375, client_h());
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

#define HOME_PW (375 - HOME_SPLIT_W) /* Split's art panel, client_h() high */
/* iPod Home (docs/ipod.md): the playing track's art beside the list. */
static struct {
    void *win, *art, *list, *sets; /* sets: the Settings list, in the list's place while open */
    void *lib; /* the Library's rows the same way: only a set with home_lib has it */
    void *stream;      /* and Streaming's */
    void *carousel[3]; /* Home: Carousel's slide_menu and its img_left, img_right */
    unsigned key, repaint; /* repaint: the timer of home_repaint */
    /* drift: home_drift's timer; drift_ms: the pan's phase, ms shown */
    unsigned drift, drift_ms, drift_last;
    /* home_tex's zoomed cover (tw 0: the still stock image, which never pans) and the panel's
     * frame, drawn from it at du dv (1/256 px, -1 for none yet); ring: home_render's rows */
    unsigned *tex;
    void *frame;
    int tw, rx, ry, u0, v0, du, dv;
    int pane; /* no cover shows: coverflow_home_clip paints the theme's pane */
    void *row; /* the highlighted row and what it shows in the pane (PANE_*) */
    void *row_image, *row_label; /* a Streaming row's image and name widgets, found once per row */
    void *svc;                   /* a card app's icon at the pane's size (pane_scaled), its name */
    char svc_name[72];
    int kind;
    /* the pane's captions, read off the wheel's path (home_counts), 0 unknown; counted: songs and
     * albums answer asked, the library_gen they were asked at; counting: 1 count_worker runs, 2 it
     * has answered in got_*; pane_read: when home_pane_read last ran, | 1, 0 never */
    int songs, albums, services, games, free_tenths, counted;
    volatile int counting;
    int got_songs, got_albums;
    unsigned long count_thread;
    unsigned asked, pane_read, count_timer, pane_timer;
    unsigned ring[4][HOME_PW];
    int clip[4];  /* the canvas clip while the art paints, restored after */
    int clipped;
} home __attribute__((section(".scratch")));
extern int ipod_home(void), ipod_home_rockbox(void), home_shortcut_open(void *, void *),
    home_lib_on(void), row_preview_on(void), library_search(void);
extern void library_rows(void *list), stream_rows(void *list);
extern void home_pane_read(int *services, int *games, int *free_tenths);
extern int library_count(const char *sql);
extern const char *ipod_home_shortcut(void);
/* Split's menu width; the art fills the rest. */

/* player_parsecover_thd writes the playing track's cover and then sets g_playcover_type, as Now
 * Playing reads it: 1 embedded, 2 folder image, 4 downloaded; 0 while parsing or stopped, 3 none.
 * Tidal's (5) is keyed by its online URL, never a queue path, so Home leaves it out. */
static const char *const player_covers[] = { 0, "file://" PEQ_ROOT "/tmp/coverpic.jpg",
                                             "file://" PEQ_ROOT "/tmp/externpic.jpg", 0,
                                             "file://" PEQ_ROOT "/tmp/externpic.jpg" };

/* The player's cover of r: its type once the cover thread has parsed r (g_lastcover_url is its
 * path), else 0, and *file that type's file, or 0. */
unsigned char now_cover(void *r, const char **file) {
    const char *path = r ? P(r, REC_PATH) : (void *)0;
    unsigned char type = path && !tk_strcmp((const char *)g_lastcover_url, path) ? g_playcover_type : 0;
    *file = type < sizeof(player_covers) / sizeof(*player_covers) ? player_covers[type] : 0;
    return type;
}

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
static void pane_invalidate(void) {
    int pane[4] = { HOME_SPLIT_W, 0, HOME_PW, client_h() }; /* rect_t, in the parent */
    widget_invalidate_force(P(home.art, W_PARENT), pane);
}
static void drift_place(void) {
    if (pan_u() == home.du && pan_v() == home.dv) return;
    pane_invalidate();
}

/* What a highlighted row shows in Split's pane (theme()->pane_icon_x): Now Playing's and the
 * Library's rows the cover, or with none the note and a count; the others their icon and
 * caption. */
enum {
    PANE_NOW,
    PANE_LIBRARY,
    PANE_SHUFFLE,
    PANE_COVERFLOW,
    PANE_FOLDER,
    PANE_GAMES,
    PANE_ROCKBOX,
    PANE_STREAM,
    PANE_SETTINGS,
    PANE_SERVICE /* Streaming's list: home.row's icon and name */
};
static const char *const pane_icons[] = {
    "classic_light_note", "classic_light_note", "classic_light_icon_note",
    "classic_light_icon_stack", "classic_light_icon_folder", "classic_light_icon_game",
    "classic_light_icon_game", "classic_light_icon_wave", "classic_light_icon_gear"
};
static int pane_covers(void) { return home.kind <= PANE_LIBRARY && !home.pane; }

/* The pan runs only while it shows: Home on top and not sliding (its snapshot would not move), the
 * screen lit, a cover that pans (the placeholder does not; Full never paints the art), on a row
 * that shows it. */
static int drift_live(void) {
    void *wm = window_manager();
    return home.tw && pane_covers() && g_backlight_status && !g_lockscreen_pageflag &&
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
    int pw = HOME_PW, ph = client_h();
    *fw = pw, *fh = ph;
    if (w && h && w <= 8192 && h <= 8192) {
        if ((unsigned)pw * h > (unsigned)ph * w)
            *fh = (int)(((unsigned)pw * h + w - 1) / w);
        else
            *fw = (int)(((unsigned)ph * w + h - 1) / h);
    }
}

/* [-1,6,-1]/4 along n opaque texels step apart, in place, rounded and clamped a channel; the ends
 * are their own neighbours. Bilinear kept a whole-pixel frame's texels and mixed a half-pixel
 * one's 50/50, so the pan's sharpness pulsed as its phase turned. home_render's cubic B-spline
 * blurs every phase about alike but is soft on its own; this brings the detail back, about twice
 * the old [1,2,1]/4 and bilinear's, and the pulse stays small (a tenth of the sharpness on a
 * photo, a fifth on lettering).
 * ponytail: the frame costs about 2.3x the bilinear's, five a second while Home is lit; an exact
 * cubic-spline prefilter (an IIR each way) sharpens no more than this does here. */
void home_sharpen(unsigned *t, int n, int step) { /* test/coverflow.py */
    unsigned prev = t[0];
    for (int i = 0; i < n; ++i) {
        unsigned c = t[i * step], next = i + 1 < n ? t[(i + 1) * step] : c, o = 0xff000000u;
        for (int s = 0; s < 24; s += 8) {
            int v = 6 * (int)(c >> s & 255) - (int)(prev >> s & 255) - (int)(next >> s & 255);
            v = (v + 2) >> 2;
            o |= (unsigned)(v < 0 ? 0 : v > 255 ? 255 : v) << s;
        }
        prev = c;
        t[i * step] = o;
    }
}

/* The decoded image (data, stride, channel order o, w x h) box filtered to tw x th texels at out,
 * ostride texels a row, over the grey paper (a byte). Alpha-weighted sums, so only the average
 * divides; at most 4 x 4 samples a texel, so a huge embedded cover filters as quickly as a small
 * one. */
static void box_filter(const unsigned char *data, unsigned stride, const unsigned char *o,
                       unsigned w, unsigned h, unsigned *out, int ostride, int tw, int th,
                       unsigned paper) {
    for (int y = 0; y < th; ++y) {
        unsigned *px_out = out + y * ostride;
        unsigned y0 = (unsigned)y * h / (unsigned)th, y1 = (unsigned)(y + 1) * h / (unsigned)th;
        y1 += y1 == y0;
        for (int x = 0; x < tw; ++x) {
            unsigned x0 = (unsigned)x * w / (unsigned)tw, x1 = (unsigned)(x + 1) * w / (unsigned)tw;
            x1 += x1 == x0;
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
            *px_out++ = 0xff000000u | (r + bg) / n | (g + bg) / n << 8 | (b + bg) / n << 16;
        }
    }
}

/* The cover at url, box filtered once to its fit zoomed DRIFT_ZOOM_PCT (home.tex, tw x th) and
 * composited over the page as Coverflow's decode does, for the pan to draw from. It pans across
 * the zoom's margin only (rx ry), from the old crop (u0 v0), so its edges never show. 1 when it
 * pans, -1 when nothing loads, 0 for a 16-bit decode or a cover too long for DRIFT_TEXELS, which
 * the stock fill shows still. home.tex holds it inside a border of its edges, a texel left and
 * above and two right and below (tw + 3 texels a row): home_render reads that far past the panel.
 * ponytail: only the playing track's cover, no iPod slideshow of library covers or crossfades;
 * library thumbnails are too small for the panel, so each change would decode a full cover. Light
 * Mode's paper behind a transparent cover changes with the next cover. */
static int home_tex(const char *url) {
    static const unsigned char at[4][4] = BITMAP_RGBA_AT;
    unsigned bitmap[64]; /* bitmap_t */
    if (widget_load_image(home.art, url, bitmap)) return -1;
    unsigned w = bitmap[0], h = bitmap[1], format = ((unsigned short *)bitmap)[7] - 1u;
    int fw, fh;
    home_fit(w, h, &fw, &fh);
    int tw = fw * DRIFT_ZOOM_PCT / 100, th = fh * DRIFT_ZOOM_PCT / 100;
    const unsigned char *data = 0;
    if (!home.frame) home.frame = opaque_frame(HOME_PW, client_h());
    if (home.frame && format < 4 && w && h && w <= 8192 && h <= 8192 && tw <= DRIFT_TEXELS / th) {
        free(home.tex);
        home.tex = calloc((unsigned)(th + 3) * (unsigned)(tw + 3), 4);
        data = home.tex ? bitmap_lock_buffer_for_read(bitmap) : 0;
    }
    if (data) {
        unsigned stride = bitmap_get_line_length(bitmap),
                 paper = ipod_light() ? scheme()->paper & 255 : 0;
        int ts = tw + 3;                   /* home.tex's row, its border included */
        unsigned *tex = home.tex + ts + 1; /* texel 0, 0 */
        box_filter(data, stride, at[format], w, h, tex, ts, tw, th, paper);
        bitmap_unlock_buffer(bitmap);
        for (int y = 0; y < th; ++y) home_sharpen(tex + y * ts, tw, 1);
        for (int x = 0; x < tw; ++x) home_sharpen(tex + x, th, ts);
        for (int y = 0; y < th; ++y) {
            unsigned *row = tex + y * ts;
            row[-1] = row[0], row[tw] = row[tw + 1] = row[tw - 1];
        }
        /* then rows -1, th and th + 1, from the edge rows and their border */
        memcpy(home.tex, home.tex + ts, (unsigned)ts * 4);
        for (int y = th + 1; y < th + 3; ++y)
            memcpy(home.tex + y * ts, home.tex + th * ts, (unsigned)ts * 4);
        home.tw = tw, home.rx = tw - fw, home.ry = th - fh;
        home.u0 = (fw - HOME_PW) / 2, home.v0 = (fh - client_h()) / 2, home.du = -1;
    }
    widget_unload_image(home.art, bitmap);
    return data != 0;
}

/* Cubic B-spline weights at phase t/256, in 1/256: all >= 0, summing to 256. */
static void bspline_w(unsigned t, unsigned *w) {
    unsigned s = 256 - t;
    w[0] = (s * s * s + 196608) / 393216;
    w[3] = (t * t * t + 196608) / 393216;
    w[1] = (3 * t * t * t + 4 * 16777216u - 6 * 256 * t * t + 196608) / 393216;
    w[2] = 256 - w[0] - w[1] - w[3];
}

/* Four opaque texels weighted w, rounded. Weights >= 0 summing to 256 keep each channel within
 * its 16 bits, so red and blue blend in one word. */
HOT unsigned tap4(unsigned a, unsigned b, unsigned c, unsigned e, const unsigned *w) {
    unsigned rb = (a & 0xff00ffu) * w[0] + (b & 0xff00ffu) * w[1] + (c & 0xff00ffu) * w[2] +
                  (e & 0xff00ffu) * w[3] + 0x800080u,
             g = (a & 0xff00u) * w[0] + (b & 0xff00u) * w[1] + (c & 0xff00u) * w[2] +
                 (e & 0xff00u) * w[3] + 0x8000u;
    return 0xff000000u | (rb >> 8 & 0xff00ffu) | (g >> 8 & 0xff00u);
}

/* The panel from the zoomed cover at u, v (1/256 px), so the pan moves by fractions of a pixel:
 * in whole pixels its 1.5 a second jerked. A separable cubic B-spline over 4 x 4 texels: each
 * cover row the panel needs is filtered across once into home.ring, and each frame row down from
 * the ring's last four. */
static void home_render(unsigned *d, int pitch, int u, int v) {
    unsigned wx[4], wy[4];
    bspline_w((unsigned)u & 255, wx), bspline_w((unsigned)v & 255, wy);
    int ts = home.tw + 3;
    const unsigned *s = home.tex + (v >> 8) * ts + (u >> 8); /* texel -1, -1 from u, v */
    for (int y = -3, h = client_h(); y < h; ++y, s += ts) {
        unsigned *o = home.ring[(y + 3) & 3];
        for (int x = 0; x < HOME_PW; ++x) o[x] = tap4(s[x], s[x + 1], s[x + 2], s[x + 3], wx);
        if (y < 0) continue;
        const unsigned *r0 = home.ring[y & 3], *r1 = home.ring[(y + 1) & 3],
                       *r2 = home.ring[(y + 2) & 3], *r3 = home.ring[(y + 3) & 3];
        for (int x = 0; x < HOME_PW; ++x) d[x] = tap4(r0[x], r1[x], r2[x], r3[x], wy);
        d += pitch;
    }
}

/* The pan's frame over the art (its image is empty), rendered again only once the pan moved. */
static void home_paint(void *canvas) {
    int u = pan_u(), v = pan_v(), r[4] = { 0, 0, HOME_PW, client_h() };
    if (u != home.du || v != home.dv) {
        unsigned *d = (unsigned *)bitmap_lock_buffer_for_write(home.frame);
        if (!d) return;
        home_render(d, (int)(bitmap_get_line_length(home.frame) / 4), u, v);
        bitmap_unlock_buffer(home.frame);
        home.du = u, home.dv = v;
    }
    canvas_draw_image(canvas, home.frame, r, r);
}

/* a to b by k of 256, per channel */
HOT unsigned lerp(unsigned a, unsigned b, unsigned k) {
    const unsigned w[4] = { 256 - k, k, 0, 0 };
    return tap4(a, b, 0, 0, w);
}

/* The t x t texture at u, v (texels from its top-left corner), bilinear, its edge texels held. */
HOT unsigned bilerp(const unsigned *tex, int t, float u, float v) {
    int x = (int)(u * 256) - 128, y = (int)(v * 256) - 128, end = (t - 1) * 256 - 1;
    x = x < 0 ? 0 : x > end ? end : x, y = y < 0 ? 0 : y > end ? end : y;
    unsigned fx = (unsigned)x & 255, fy = (unsigned)y & 255,
             w[4] = { (256 - fx) * (256 - fy) >> 8, fx * (256 - fy) >> 8, (256 - fx) * fy >> 8, 0 };
    w[3] = 256 - w[0] - w[1] - w[2];
    const unsigned *p = tex + (y >> 8) * t + (x >> 8);
    return tap4(p[0], p[1], p[t], p[t + 1], w);
}

/* Classic's Now Playing art (docs/internals.md#slanted-art), an iPod classic's: the cover turned in
 * perspective, its near (left) edge the art's side high, its far edge SLANT_FAR/256 of that,
 * centred; under it the reflection, each column mirrored about its own bottom edge, from
 * SLANT_REFLECT_A/256 fading to the paper over SLANT_REFLECT_PCT of the column's height. Into d
 * (pitch pixels a row), side wide and rows high, from tex, the cover box filtered to t x t, twice
 * the art: each pixel is two half columns of two bilinear taps, the slanted edges by coverage.
 * test/coverflow.py */
void slant_render(unsigned *d, int pitch, int side, int rows, const unsigned *tex, int t,
                  unsigned paper) {
    unsigned bg = 0xff000000u | paper * 0x010101u;
    float near = (float)side, far = side * SLANT_FAR / 256.f;
    for (int x = 0; x < side; ++x)
        for (int half = 0; half < 2; ++half) {
            /* u: the texture is linear in depth, so in 1 / h */
            float c = x + .25f + .5f * half, h = near - (near - far) * c / near,
                  top = (near - h) / 2, bot = top + h, fade = h * SLANT_REFLECT_PCT / 100,
                  u = t * (near - h) * far / (h * (near - far)), k = t / h;
            for (int y = 0; y < rows; ++y) {
                /* the pixel's shares of the cover, the reflection and the paper, of 256 */
                unsigned cover = bg, mirror = bg, w[4] = { 0, 0, 256, 0 };
                float a = y > top ? y : top, b = y + 1 < bot ? y + 1 : bot, q = (b - a) / 4;
                if (b > a) {
                    cover = lerp(bilerp(tex, t, u, (a + q - top) * k),
                                 bilerp(tex, t, u, (b - q - top) * k), 128);
                    w[0] = (unsigned)((b - a) * 256);
                }
                a = y > bot ? y : bot, b = y + 1 < bot + fade ? y + 1 : bot + fade, q = (b - a) / 4;
                if (b > a) {
                    unsigned s = lerp(bilerp(tex, t, u, t - (a + q - bot) * k),
                                      bilerp(tex, t, u, t - (b - q - bot) * k), 128);
                    mirror = lerp(bg, s, (unsigned)(SLANT_REFLECT_A * (1 - ((a + b) / 2 - bot) / fade)));
                    w[1] = (unsigned)((b - a) * 256);
                }
                w[2] = 256 - w[0] - w[1];
                unsigned out = tap4(cover, mirror, bg, bg, w), *px = d + y * pitch + x;
                *px = half ? lerp(*px, out, 128) : out;
            }
        }
}

/* Classic's art over stock's flat cover, on the page's grey paper (a byte): slant.frame, rendered
 * again only when the cover (its url, size and 8 x 8 of its pixels: the player writes every track's
 * cover to the same file, so its name tells none apart) or the paper changes. The one frame serves
 * local Now Playing and Classic's Spotify and Internet Radio pages (navigation.c stream_ipod): the
 * key says whose cover it holds, so a page coming back redraws its own. Its layout (fixed
 * while the page is open) makes the art's widget taller
 * than wide (tools/ipod.py np_ipod), room for the reflection under it, so a repaint of the bar
 * below repaints it too.
 * ponytail: stock still draws its flat cover under the frame each paint, as it always did; a 16-bit
 * decode, or no memory for the frame or texture, leaves stock's, scale_auto in the taller box. */
static struct {
    void *frame;
    unsigned key;
} slant;

void coverflow_slant_close(void) {
    if (slant.frame) bitmap_destroy(slant.frame);
    memset(&slant, 0, sizeof slant);
}

void coverflow_slant(void *w, void *canvas, unsigned paper) {
    static const unsigned char at[4][4] = BITMAP_RGBA_AT;
    unsigned bitmap[64]; /* bitmap_t */
    const char *url = widget_get_prop_str(w, "image", (void *)0);
    if (!url || !*url || widget_load_image(w, url, bitmap)) return; /* stock's copy: kept loaded */
    unsigned bw = bitmap[0], bh = bitmap[1], format = ((unsigned short *)bitmap)[7] - 1u;
    const unsigned char *data =
        format < 4 && bw && bh && bw <= 8192 && bh <= 8192 ? bitmap_lock_buffer_for_read(bitmap) : 0;
    if (!data) return;
    int side = I(w, W_W), rows = I(w, W_H), t = 2 * side;
    unsigned stride = bitmap_get_line_length(bitmap),
             key = hash_bytes(fnv(FNV_SEED, (const unsigned char *)url),
                              (const unsigned char *)bitmap, 8) ^
                   paper;
    for (unsigned j = 1; j < 16; j += 2)
        for (unsigned i = 1; i < 16; i += 2)
            key = hash_bytes(key, data + bh * j / 16 * stride + bw * i / 16 * 4, 4);
    const unsigned *f = slant.frame; /* bitmap_t: width @0, height @4 */
    if (f && (f[0] != (unsigned)side || f[1] != (unsigned)rows)) coverflow_slant_close();
    if (!slant.frame) slant.frame = opaque_frame(side, rows);
    unsigned *tex = slant.frame && key != slant.key ? calloc((unsigned)(t * t), 4) : 0;
    unsigned *d = tex ? (unsigned *)bitmap_lock_buffer_for_write(slant.frame) : 0;
    if (d) { /* fitted whole and centred on the paper */
        unsigned fit = bw > bh ? bw : bh;
        int tw = (int)(t * bw / fit), th = (int)(t * bh / fit);
        for (int i = 0; i < t * t; ++i) tex[i] = 0xff000000u | paper * 0x010101u;
        box_filter(data, stride, at[format], bw, bh, tex + (t - th) / 2 * t + (t - tw) / 2, t, tw,
                   th, paper);
        slant_render(d, (int)(bitmap_get_line_length(slant.frame) / 4), side, rows, tex, t, paper);
        bitmap_unlock_buffer(slant.frame);
        slant.key = key;
    }
    free(tex);
    bitmap_unlock_buffer(bitmap);
    int r[4] = { 0, 0, side, rows };
    if (slant.frame && slant.key == key) canvas_draw_image(canvas, slant.frame, r, r);
}

/* The theme's pane shadow down the panel's left edge at x, y. */
static void pane_shadow(void *canvas, int x, int y, int h) {
    const theme_t *T = theme();
    int sw = T->pane_shadow_w;
    for (int i = 0; i < sw; ++i) {
        stock_canvas_set_fill_color(canvas, (RGBA(T->pane_shadow) & 0xffffff) |
                                                (unsigned)T->pane_shadow_a[i] << 24);
        canvas_fill_rect(canvas, x + i, y, 1, h);
    }
}

/* n of what the caption counts, in t: "1 Song", "12 Songs"; 0: unknown, none. */
static int pane_count(char *t, unsigned size, unsigned n, const char *one, const char *many) {
    return !n ? 0 : tk_snprintf(t, size, n == 1 ? one : many, n);
}

/* The caption of the row's pane in t, its length. */
static int pane_caption(char *t, unsigned size) {
    unsigned at, n;
    switch (home.kind) {
    case PANE_NOW:
        queue_now(&at, &n);
        return pane_count(t, size, n, "1 Song", "%u Songs");
    case PANE_LIBRARY:
    case PANE_SHUFFLE:
        return pane_count(t, size, (unsigned)home.songs, "1 Song", "%u Songs");
    case PANE_COVERFLOW:
        return pane_count(t, size, (unsigned)home.albums, "1 Album", "%u Albums");
    case PANE_GAMES:
        return pane_count(t, size, (unsigned)home.games, "1 Game", "%u Games");
    case PANE_STREAM:
        return pane_count(t, size, (unsigned)home.services, "1 Service", "%u Services");
    case PANE_SETTINGS:
        return home.free_tenths ? tk_snprintf(t, size, "%u.%u GB Free", home.free_tenths / 10,
                                              home.free_tenths % 10)
                                : 0;
    }
    return 0;
}

/* w's first child of type, or 0. */
static void *child_of_type(void *w, const char *type) {
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i) {
        void *c = widget_get_child(w, i);
        if (!tk_strcmp(widget_get_type(c), type)) return c;
    }
    return (void *)0;
}

/* A Streaming row's image widget for the pane: a card app's own file, or the build's copy of a
 * fixed service's icon at the pane's size (tools/build.py), in t. */
static const char *service_icon(void *image, char *t, unsigned size) {
    const char *icon = image ? widget_get_prop_str(image, "image", "") : "";
    if (tk_str_start_with(icon, "file://")) return icon;
    tk_snprintf(t, size, "classic_light_%s", icon);
    return t;
}

/* bitmap, the icon named name, bilinear at the theme's px, once for a name, so the paints after it
 * only draw it: alpha-weighted, as its edge's transparent pixels hold no colour. bitmap itself when
 * that can't be done.
 * ponytail: one icon's frame, kept once made; the row's next app scales again. */
static void *pane_scaled(void *bitmap, const char *name, unsigned px) {
    static const unsigned char at[4][4] = BITMAP_RGBA_AT;
    unsigned *b = bitmap, w = b[0], h = b[1], format = ((unsigned short *)bitmap)[7] - 1u;
    if (!tk_strcmp(home.svc_name, name)) return home.svc;
    if (format >= 4 || !w || !h || w > 512 || h > 512) return bitmap;
    if (!home.svc) home.svc = bitmap_create_ex(px, px, px * 4, 1 /* RGBA8888 */);
    const unsigned char *data = home.svc ? bitmap_lock_buffer_for_read(bitmap) : 0;
    unsigned *d = data ? (unsigned *)bitmap_lock_buffer_for_write(home.svc) : 0;
    if (!d) {
        if (data) bitmap_unlock_buffer(bitmap);
        return bitmap;
    }
    const unsigned char *o = at[format];
    unsigned stride = bitmap_get_line_length(bitmap), line = bitmap_get_line_length(home.svc) / 4;
    for (unsigned y = 0; y < px; ++y) {
        /* the pixels' centres, in 1/256 of a source pixel */
        unsigned sy = (2 * y + 1) * h * 128 / px, y0 = sy < 128 ? 0 : (sy - 128) >> 8;
        unsigned fy = sy < 128 ? 0 : (sy - 128) & 255, y1 = y0 + 1 < h ? y0 + 1 : y0;
        for (unsigned x = 0; x < px; ++x) {
            unsigned sx = (2 * x + 1) * w * 128 / px, x0 = sx < 128 ? 0 : (sx - 128) >> 8;
            unsigned fx = sx < 128 ? 0 : (sx - 128) & 255, x1 = x0 + 1 < w ? x0 + 1 : x0;
            const unsigned char *r0 = data + y0 * stride, *r1 = data + y1 * stride;
            const unsigned char *s[4] = { r0 + x0 * 4, r0 + x1 * 4, r1 + x0 * 4, r1 + x1 * 4 };
            unsigned k[4] = { (256 - fx) * (256 - fy), fx * (256 - fy), (256 - fx) * fy, fx * fy };
            unsigned r = 0, g = 0, bl = 0, a = 0; /* each under 2^16 x 255 x 255 */
            for (int i = 0; i < 4; ++i) {
                unsigned v = k[i] * s[i][o[3]];
                r += v * s[i][o[0]], g += v * s[i][o[1]], bl += v * s[i][o[2]], a += v;
            }
            d[y * line + x] = a ? (a >> 16) << 24 | bl / a << 16 | g / a << 8 | r / a : 0;
        }
    }
    bitmap_unlock_buffer(home.svc);
    bitmap_unlock_buffer(bitmap);
    tk_snprintf(home.svc_name, sizeof home.svc_name, "%s", name);
    return home.svc;
}

/* The pane on a Streaming row: its icon, a card app's scaled to the theme's size, else the card
 * apps' own, and its name in the count's place. A name too wide for the count's column shrinks to
 * three quarters of its size, then loses its end. A card app's file, up to 512 px square, leaves
 * the image cache once drawn or scaled: its scaled copy is what stays.
 * ponytail: a card app highlighted again after another decodes its file again; a scaled copy per
 * app if that shows on the wheel. */
static void pane_service(void *canvas, int x, int y) {
    const theme_t *T = theme();
    unsigned bitmap[64], s[33], n = 0; /* bitmap_t */
    char t[72];
    if (!home.row) return;
    const char *icon = service_icon(home.row_image, t, sizeof t);
    int side = T->pane_svc_px, card = tk_str_start_with(icon, "file://");
    int src[4] = { 0, 0, side, side },
        dst[4] = { x + T->pane_svc_x, y + T->pane_svc_y, side, side };
    if (card && home.svc && !tk_strcmp(home.svc_name, icon)) {
        canvas_draw_image(canvas, home.svc, src, dst);
    } else {
        int own = !widget_load_image(home.art, icon, bitmap);
        if (own || !widget_load_image(home.art, "classic_light_stream_app", bitmap)) {
            void *art = own && (int)bitmap[0] != side && card
                            ? pane_scaled(bitmap, icon, (unsigned)side)
                            : bitmap;
            if (art == bitmap) src[2] = (int)bitmap[0], src[3] = (int)bitmap[1];
            canvas_draw_image(canvas, art, src, dst);
        }
        if (own && card) widget_unload_image(home.art, bitmap);
    }
    void *label = home.row_label;
    const unsigned *name = label ? widget_get_text(label) : (const unsigned *)0;
    while (name && name[n] && n < 32) s[n] = name[n], ++n;
    if (!n) return;
    int box[4] = { x, y + T->pane_count_y, T->pane_count_w, 24 }, px = T->pane_count_px, face = px;
    canvas_set_font(canvas, theme_face(s, (int)n, &face), (unsigned)face);
    float w = canvas_measure_text(canvas, s, n);
    if (w > box[2]) {
        int fit = (int)((float)px * box[2] / w), least = px * 3 / 4;
        face = px = fit > least ? fit : least;
        canvas_set_font(canvas, theme_face(s, (int)n, &face), (unsigned)face);
        while (n > 1 && canvas_measure_text(canvas, s, n) > box[2]) s[--n - 1] = 0x2026;
    }
    draw_centred(canvas, s, n, box, (unsigned)px, RGBA(ipod_light() ? 0u : 0xffffffu));
}

/* Split's panel in the theme's home_pane, the half's: the stops, its light, a shadow down its left edge, the
 * row's image from the theme (tools/art.py) and its caption. Raw colours, but the caption's: ui_ink
 * flips black to the light half's white paper, and the dark half draws white as it is. */
void home_pane_paint(void *canvas, int x, int y, int w, int h) {
    const theme_t *T = theme();
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR), bitmap[64]; /* bitmap_t */
    paint_stops(canvas, x, y, w, h, &T->home_pane[ipod_light()]);
    /* the light: a band per alpha, as eight bits can tell no finer */
    for (int a = T->pane_light_a, n = a; a > 0; --a) {
        int x0 = w * (n - a) / n;
        stock_canvas_set_fill_color(canvas, 0xffffffu | (unsigned)a << 24);
        canvas_fill_rect(canvas, x + x0, y, w * (n - a + 1) / n - x0, h);
    }
    pane_shadow(canvas, x, y, h);
    if (home.kind == PANE_SERVICE) {
        pane_service(canvas, x, y);
        stock_canvas_set_fill_color(canvas, fill);
        return;
    }
    if (!widget_load_image(home.art, pane_icons[home.kind], bitmap)) {
        int note = home.kind <= PANE_SHUFFLE;
        int src[4] = { 0, 0, (int)bitmap[0], (int)bitmap[1] },
            dst[4] = { x + (note ? T->pane_note_x : T->pane_icon_x),
                       y + (note ? T->pane_note_y : T->pane_icon_y), src[2], src[3] };
        canvas_draw_image(canvas, bitmap, src, dst);
    }
    char t[20];
    int len = pane_caption(t, sizeof t);
    if (len > 0) {
        unsigned s[20];
        for (int i = 0; i < len; ++i) s[i] = (unsigned char)t[i];
        int box[4] = { x, y + T->pane_count_y, T->pane_count_w, 24 }; /* rect_t */
        draw_centred(canvas, s, (unsigned)len, box, T->pane_count_px,
                     RGBA(ipod_light() ? 0u : 0xffffffu));
    }
    stock_canvas_set_fill_color(canvas, fill);
}

/* A paint can't invalidate (AWTK drops it with the frame), so the frame after. */
static int pane_after(const void *unused) {
    home.pane_timer = 0;
    pane_invalidate();
    return 0; /* RET_REMOVE */
}

/* The row now highlighted on Home's surface: the pane follows it. No I/O: its image is in the image
 * cache (home_counts) and its caption in home. On a wheel move (painting 0) the pane joins the
 * row's frame; a paint catches every other way a row changes (Return's first row, a tap, a list
 * sliding in). Names are compared only when the row changes. */
void coverflow_home_row(void *surface, void *row, int id, int painting) {
    if (row == home.row || !home.art || !theme()->pane_icon_x || ipod_home()) return;
    home.row = row;
    void *list = P(surface, W_PARENT);
    const char *name = widget_get_prop_str(row, "name", "");
    int kind = PANE_LIBRARY;
    if (!row_preview_on()) kind = PANE_NOW; /* Row Preview off: Now Playing's on every row */
    else if (list == home.sets) kind = PANE_SETTINGS;
    else if (list == home.lib) kind = id == library_search() ? PANE_SHUFFLE : PANE_LIBRARY;
    else if (list == home.stream) {
        kind = PANE_SERVICE;
        home.row_image = child_of_type(row, "image");
        home.row_label = child_of_type(row, "hscroll_label");
    } else {
        static const char *const names[] = { "img_playing",   "img_library",  0,
                                             "img_coverflow", "img_folder",   0,
                                             "img_rockbox",   "img_services", "img_settings" };
        for (unsigned i = 0; i < sizeof names / sizeof *names; ++i)
            if (names[i] && !tk_strcmp(name, names[i])) kind = (int)i;
        /* a set without home_lib: stock's name */
        if (!tk_strcmp(name, "img_stream")) kind = PANE_STREAM;
        if (kind == PANE_ROCKBOX) { /* the Shortcut row: the Games icon, its count only for Games;
                                     * the Podcasts and Audiobooks folders, Folder's icon */
            const char *s = ipod_home_shortcut();
            kind = !tk_strcmp(s, "Games")     ? PANE_GAMES
                   : !tk_strcmp(s, "Rockbox") ? PANE_ROCKBOX
                   : !tk_strcmp(s, "Shuffle") ? PANE_SHUFFLE
                   : !tk_strcmp(s, "Podcasts") || !tk_strcmp(s, "Audiobooks") ? PANE_FOLDER
                                              : PANE_LIBRARY;
        }
    }
    if (kind == home.kind && kind != PANE_SERVICE) return;
    home.kind = kind;
    if (painting)
        rearm(&home.pane_timer, pane_after, 0);
    else
        pane_invalidate();
}

#define PANE_COUNT_MS 400 /* once Home has slid back */
#define PANE_BOOT_MS 1500 /* once boot's first paints are over */
#define PANE_READ_MS 60000 /* the card's I/O, at most this often */
/* The two counts, a whole library's grouping among them, take seconds on a large one: off the UI
 * thread, and home_counts publishes them. -1: no answer. */
static void *count_worker(void *unused) {
    home.got_songs = library_count("select count(*) from songtable where " MUSIC_WHERE);
    /* Coverflow's rows: a row per album, and one for all the songs with no album */
    if (home.got_albums < 0)
        home.got_albums = library_count("select sum(e=0)+max(e) from (select ifnull(album,'')='' e "
                                        "from songtable where " MUSIC_WHERE ALBUM_GROUP ")");
    home.counting = 2;
    return 0;
}

/* The pane's captions and images, once Home's window is back on top and after boot: not on a wheel
 * move, and at most one query per library change, or per Home after one that failed. A scan
 * running: the counts it will change are unknown, and this looks again until it ends. Home in Full
 * or Carousel, or Row Preview off, shows no count and asks nothing; the next foreground asks.
 * ponytail: home_pane_read's card I/O stays on the UI thread (addon_scan's table is the UI's) at
 * most once a PANE_READ_MS, so a fresh app or the free space can lag that long. */
static int home_counts(const void *unused) {
    int answered = home.counting == 2;
    if (answered) {
        pthread_join(home.count_thread, 0);
        home.counting = 0;
        home.counted = home.got_songs >= 0 && home.got_albums >= 0;
        if (home.counted) home.songs = home.got_songs, home.albums = home.got_albums;
        pane_invalidate();
    }
    if (home.counting) return 8; /* RET_REPEAT */
    unsigned gen = library_gen;
    if (library_scanning()) {
        if (home.songs || home.albums) pane_invalidate();
        home.songs = home.albums = home.counted = 0;
        return 8;
    }
    int pane = !ipod_home() && row_preview_on(); /* the counts' only reader */
    if (pane && (gen != home.asked || (!home.counted && !answered))) {
        home.asked = gen;
        /* Coverflow's own list when it has one */
        home.got_albums = cf.stock && !cf.scoped && cf.albums_gen == gen ? (int)deque_size(cf.stock) : -1;
        home.counting = 1;
        if (!pthread_create(&home.count_thread, 0, count_worker, 0)) return 8;
        home.counting = 0;
    }
    home.count_timer = 0;
    if (!pane) return 0; /* RET_REMOVE */
    unsigned now = time_now_ms();
    if (!home.pane_read || now - home.pane_read >= PANE_READ_MS) {
        home.pane_read = now | 1;
        home_pane_read(&home.services, &home.games, &home.free_tenths);
        unsigned bitmap[64]; /* bitmap_t: decoded once, so no row's first show reads it */
        for (unsigned i = 0; i < sizeof pane_icons / sizeof *pane_icons; ++i)
            widget_load_image(home.art, pane_icons[i], bitmap);
        pane_invalidate();
    }
    return 0;
}

static int home_foreground(void *ctx, void *event) {
    rearm(&home.count_timer, home_counts, PANE_COUNT_MS);
    home.row = 0; /* the pane's kind again: Row Preview may have changed in Settings */
    return 0;
}

/* The player's cover, else the Coverflow cache of the track's album (now_tag), else the theme's
 * pane or the placeholder. The player's files belong to the track whose path it copies to
 * g_lastcover_url after writing them, so right after a track change they count only once that is
 * this track. Runs whenever a window or the status bar paints (at least once a second), for Home
 * in Split, and reloads only when the track, the cover it can use or its parsed tags change;
 * home_repaint then shows the new art whole. */
void coverflow_home_art(void *top) {
    if (!home.art || top != home.win || !widget_get_visible(home.art)) return;
    if (!home.drift && drift_live()) {
        home.drift_last = time_now_ms();
        home.drift = timer_add(home_drift, (void *)0, DRIFT_TICK_MS);
    }
    unsigned pos, n;
    void *r = queue_now(&pos, &n);
    const char *path = r ? P(r, REC_PATH) : (void *)0;
    const char *cover;
    unsigned char type = now_cover(r, &cover);
    unsigned album = tags_key(now_tag(r, REC_ARTIST), now_tag(r, REC_ALBUM));
    unsigned key = hash_bytes(hash_bytes(fnv(FNV_SEED, (const unsigned char *)path), &type, 1),
                              (const unsigned char *)&album, sizeof(album));
    if (key == home.key) return;
    home.key = key;
    home.pane = 0;
    char url[600] = "file://";
    if (r) art_path(url + 7, album, "");
    unsigned size[2] = { 0, 0 };
    int ch = client_h(), fw = HOME_PW, fh = ch;
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
        int shown = pans == 0 && image_show(home.art, src, size);
        if (!shown && theme()->home_pane[ipod_light()].n) {
            image_base_set_image(home.art, ""); /* home_pane_paint draws it, the whole panel */
            home.pane = 1;
        } else {
            /* still, sized to the image's fit and centred, so the native fill draws it whole and
             * the clip crops it evenly */
            if (!shown && !image_show(home.art, HOME_PLACEHOLDER, size))
                image_base_set_image(home.art, HOME_PLACEHOLDER);
            home_fit(size[0], size[1], &fw, &fh);
        }
    }
    widget_move_resize(home.art, HOME_SPLIT_W + (HOME_PW - fw) / 2, (ch - fh) / 2, fw, fh);
    rearm(&home.repaint, home_repaint, 0);
}

/* The art paints only inside its panel and the lists only left of it, so a sliding list
 * never covers the art: ringnav_paint_bg narrows the canvas clip (screen coordinates; the canvas
 * origin is the widget's) before stock draws it, and ringnav_paint puts the old clip back after its
 * children, the pan's frame painted inside it first. */
void coverflow_home_clip(void *w, void *canvas, int begin) {
    if (!w ||
        (w != home.art && w != home.list && w != home.sets && w != home.lib && w != home.stream))
        return;
    if (!begin) {
        int covers = pane_covers();
        if (w == home.art && covers && home.tw) home_paint(canvas);
        /* The art's box is the image's fit, so the panel's edge and rows are measured from it. */
        else if (w == home.art && !covers)
            home_pane_paint(canvas, HOME_SPLIT_W - I(w, W_X), -I(w, W_Y), HOME_PW, client_h());
        /* a cover: the pane's own shadow over it, where the left column meets it */
        if (w == home.art && covers && theme()->pane_shadow_w) {
            unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
            pane_shadow(canvas, HOME_SPLIT_W - I(w, W_X), -I(w, W_Y), client_h());
            stock_canvas_set_fill_color(canvas, fill);
        }
        if (home.clipped) canvas_set_clip_rect(canvas, home.clip);
        home.clipped = 0;
        return;
    }
    int clip[4], art = w == home.art, x = HOME_SPLIT_W, pane = widget_get_visible(home.art) ? x : 375;
    clip_within(canvas, home.clip, clip, I(canvas, CANVAS_X) - I(w, W_X) + (art ? x : 0),
                I(canvas, CANVAS_Y) - I(w, W_Y) + (art ? 0 : I(w, W_Y)), art ? 375 - x : pane,
                art ? client_h() : I(w, W_H));
    canvas_set_clip_rect(canvas, clip);
    home.clipped = 1;
}

/* Whether w is in one of Home's menus (Minimal draws their labels, paint_caps). */
int coverflow_home_owns(void *w) {
    for (; w && home.list; w = P(w, W_PARENT))
        if (w == home.list || w == home.sets || w == home.lib || w == home.stream) return 1;
    return 0;
}

/* Both menus' lists span outer, their rows and tap targets inner; labels start at x and end end
 * before the row's edge (Classic: before the chevron). */
static void home_width(void *w, int outer, int inner, int x, int end, int depth) {
    widget_move_resize(w, I(w, W_X), depth ? I(w, W_Y) : theme()->home_top,
                       depth < 2 ? outer : inner, I(w, W_H));
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i) {
        void *child = widget_get_child(w, i);
        if (!tk_strcmp(widget_get_type(child), "hscroll_label")) {
            widget_move_resize(child, x, I(child, W_Y), inner - x - end, I(child, W_H));
            /* Classic's face; Minimal paints its own (paint_caps) */
            theme_font(child, ROLE_HOME, -1);
        }
        else
            home_width(child, outer, inner, x, end, depth + 1);
    }
}

/* Shown, or hidden and taking no taps: hidden widgets keep theirs. */
static void home_show(void *w, int on) {
    widget_set_visible(w, on, 0);
    widget_set_sensitive(w, on);
}

/* Stored HOME=0/1/2 is Split/Full/Carousel in every theme: Split halves the screen, the art in the
 * right panel; Full hides the art and widens the list, its rows to the theme's home_full_row;
 * Carousel hides the art and shows stock's cards (tools/ipod.py ipod_home) in the
 * lists' place, and leaving it, the main list. Hidden Rockbox is restacked last because the native
 * layout includes hidden rows, and made insensitive, as hidden rows keep their taps. */
void coverflow_home_layout(void) {
    int mode = ipod_home(), full = mode == 1, carousel = mode == 2;
    const theme_t *T = theme();
    /* The art goes back to the panel; Split's next paint resumes the pan. */
    stop_timer(&home.drift);
    if (!home.list) return;
    void *rockbox = widget_lookup(home.win, "btn_rockbox", 1);
    if (rockbox) {
        int shown = ipod_home_rockbox();
        widget_restack(rockbox, shown ? 4 : 6); /* after Folder, else last */
        home_show(rockbox, shown); /* hidden, it would still take Streaming's taps */
        widget_set_text_utf8(widget_lookup(home.win, "label_rockbox", 1), ipod_home_shortcut());
        /* ponytail: the Shortcut card stays when the row hides (Shortcut: Rockbox, no Rockbox on
         * the card; its tap says so), as the slide_menu lays out hidden cards too; removing it
         * would mean re-adding it once the card has Rockbox */
        if (home.carousel[0])
            widget_set_text_utf8(widget_lookup(home.carousel[0], "label_rockbox", 1),
                                 ipod_home_shortcut());
    }
    int split = HOME_SPLIT_W, outer = full ? 375 : split,
        inner = !full ? split : T->home_full_row, x = T->home_text_x,
        end = T->home_label_end[full];
    home_width(home.list, outer, inner, x, end, 0);
    if (home.sets) home_width(home.sets, outer, inner, x, end, 0);
    widget_move_resize(home.art, split, 0, 375 - split, client_h());
    widget_set_visible(home.art, !mode, 0);
    for (int i = 0; mode && i < 2; ++i) { /* Split's alone */
        void *open = i ? home.stream : home.lib;
        if (!open || !widget_get_visible(open)) continue;
        home_show(open, 0);
        home_show(home.list, 1);
    }
    if (home.carousel[0] && carousel != widget_get_visible(home.carousel[0])) {
        for (int i = 0; i < 3; ++i) home_show(home.carousel[i], carousel);
        home_show(home.list, !carousel);
        if (home.sets) home_show(home.sets, 0);
    }
    home.key = ~0u; /* Split shows the current cover again, next frame if laid out mid-paint */
    rearm(&home.repaint, home_repaint, 0);
}

/* w from x+dx to its x over m's ms and easing. Returns 0 without touching w when m->ms is 0;
 * returns 0 with w back at its x when no animator can be made; else 1. */
static int push_x(void *w, int dx, const motion_t *m) {
    if (!m->ms) return 0;
    int x = I(w, W_X);
    widget_move_resize(w, x + dx, I(w, W_Y), I(w, W_W), I(w, W_H));
    void *a = widget_animator_prop_create(w, m->ms, 0, m->easing, "x");
    if (!a) {
        widget_move_resize(w, x, I(w, W_Y), I(w, W_W), I(w, W_H));
        return 0;
    }
    widget_animator_prop_set_params(a, x + dx, x);
    widget_animator_start(a);
    return 1;
}

/* After page_list rebuilt a level: its list (view's list_view parent; the title is the bar's under
 * Classic) slides in from the right (dir 1, deeper) or the left (-1, back) over the theme's
 * inplace motion. No window animates, so keys and the wheel act on the new list at once. 0 for a
 * null view or dir 0, when the theme keeps levels still (push_x touches nothing), or when no
 * animator could be made: the list is at x 0. */
int page_push(void *view, int dir) {
    if (!theme()->motion[MOTION_INPLACE].ms) return 0;
    void *w = view ? P(view, W_PARENT) : (void *)0;
    if (!w || !dir) return 0;
    /* A kept level (Photos', a folder's table) still sliding goes to its rest first. */
    if (I(w, W_X)) widget_move_resize(w, 0, I(w, W_Y), I(w, W_W), I(w, W_H));
    return push_x(w, dir * I(w, W_W), &theme()->motion[MOTION_INPLACE]);
}

/* Home's Settings row, as an iPod's submenu: the Settings list (stock's Playback and System
 * setting rows, which stock binds) slides in from the right over the list's place, the art staying;
 * Return (coverflow_home_back) slides the list back in from the left. Each list keeps its row. */
static void home_swap(void *from, void *to, int dx) {
    home_show(from, 0); /* only the shown list takes taps */
    /* A list still sliding goes to its rest first: push_x's goal, and where no push leaves it. */
    if (I(to, W_X)) widget_move_resize(to, 0, I(to, W_Y), I(to, W_W), I(to, W_H));
    push_x(to, dx, &theme()->motion[MOTION_SWAP]);
    home_show(to, 1);
    bar_title_sync(home.win); /* the bar's text follows the list at once, not at its next second */
}

static int home_settings(void *ctx, void *event) {
    if (home.sets && !widget_get_visible(home.sets)) home_swap(home.list, home.sets, I(home.list, W_W));
    return 0;
}

/* A list its page's constructor just filled, slid in in Home's rows' shape. service: Streaming's,
 * whose 70 px rows take Home's row height, and the build's icons are decoded now, not on the
 * wheel's path (coverflow_home_row). */
static void home_fill(void *list, int service) {
    const theme_t *T = theme();
    home_width(list, HOME_SPLIT_W, HOME_SPLIT_W, T->home_text_x, T->home_label_end[0], 0);
    void *view = widget_count_children(list) ? widget_get_child(list, 0) : (void *)0;
    void *first = service ? widget_get_child(widget_get_child(home.list, 0), 0) : (void *)0;
    for (unsigned i = 0, n = view ? widget_count_children(view) : 0; i < n; ++i) {
        void *row = widget_get_child(view, i);
        void *b = widget_count_children(row) ? widget_get_child(row, 0) : (void *)0;
        if (!b) continue;
        int h = service ? I(first, W_H) : I(b, W_H);
        widget_move_resize(b, 0, service ? 0 : I(b, W_Y), HOME_SPLIT_W, h);
        if (service) {
            void *label = child_of_type(b, "hscroll_label");
            unsigned bitmap[64]; /* bitmap_t */
            char t[72];
            const char *icon = service_icon(child_of_type(b, "image"), t, sizeof t);
            if (label) widget_move_resize(label, I(label, W_X), 0, I(label, W_W), I(b, W_H));
            /* the build's icons; a card app's file waits for the pane (pane_service) */
            if (!tk_str_start_with(icon, "file://")) widget_load_image(home.art, icon, bitmap);
        }
    }
    widget_layout(list);
    home_swap(home.list, list, I(home.list, W_W));
}

/* Home's Library row in Split (a set with home_lib, Library on Home on): its rows slide in as Settings' do, each filled
 * by the page's own constructor (home.c library_rows) in Home's rows' shape, and open what
 * they open from the page. Full and Carousel keep the page. */
static int home_library(void *ctx, void *event) {
    if (ipod_home() || !home.lib || !home_lib_on()) return on_home_music_onclick(ctx, event);
    if (widget_get_visible(home.lib)) return 0;
    library_rows(home.lib);
    home_fill(home.lib, 0);
    return 0;
}

/* Home's Streaming row, the same way (home.c stream_rows). The rebuild frees the rows, and
 * home.row is read only under PANE_SERVICE, so that goes first. */
static int home_streaming(void *ctx, void *event) {
    if (ipod_home() || !home.stream) return on_home_stream_onclick(ctx, event);
    if (widget_get_visible(home.stream)) return 0;
    home.kind = PANE_STREAM;
    home.row = (void *)0;
    stream_rows(home.stream);
    home_fill(home.stream, 1);
    return 0;
}

/* The list open on Home, 0: its main list. */
static void *home_open(void) {
    return home.lib && widget_get_visible(home.lib)         ? home.lib
           : home.stream && widget_get_visible(home.stream) ? home.stream
           : home.sets && widget_get_visible(home.sets)     ? home.sets
                                                            : (void *)0;
}

/* Return on Home with a list open: 1 when it went back to the main list. */
int coverflow_home_back(void *top) {
    void *open = top == home.win ? home_open() : (void *)0;
    if (!open) return 0;
    home_swap(open, home.list, -I(home.list, W_W));
    return 1;
}

/* A hold going Home (navigation.c compact_now_playing) from under a list open there: the main list,
 * with no slide, as the windows over it are about to close. Only while this Home is the bottom
 * window but the bar (application_init's first), so its lists are alive. */
void coverflow_home_top(void) {
    void *wm = window_manager();
    unsigned n = widget_count_children(wm),
             bottom = n && widget_get_child(wm, 0) == *(void *const *)system_bar;
    if (!home.win || bottom >= n || widget_get_child(wm, bottom) != home.win) return;
    void *open = home_open();
    if (!open) return;
    home_show(open, 0);
    home_show(home.list, 1);
}

/* Classic's bar title on Home (navigation.c bar_title_sync): coverflow_home_name on its own list. */
const char coverflow_home_name[] = "Q2 Pod";
const char *coverflow_home_title(void) {
    void *open = home_open();
    return !open                 ? coverflow_home_name
           : open == home.lib    ? "Library"
           : open == home.stream ? "Streaming"
                                 : "Settings";
}

/* home_page_init: stock binds the name-matched img_* cards, then the Coverflow card binds here,
 * on its image as stock does, whatever stock returned. */
int coverflow_home(void *win, void *ctx) {
    int result = stock_home_trampoline(win, ctx);
    widget_on(widget_lookup(win, "img_coverflow", 1), EVT_CLICK, coverflow_open, 0);
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
    home.lib = widget_lookup(win, "list_view_homelib", 1);
    if (home.lib) {
        widget_set_sensitive(home.lib, 0);
        widget_on(widget_lookup(win, "img_library", 1), EVT_CLICK, home_library, win);
    }
    home.stream = widget_lookup(win, "list_view_homestream", 1);
    if (home.kind == PANE_SERVICE) home.kind = PANE_STREAM; /* the last Home's rows */
    if (home.stream) {
        widget_set_sensitive(home.stream, 0);
        widget_on(widget_lookup(win, "img_services", 1), EVT_CLICK, home_streaming, win);
    }
    static const char *const carousel[] = { "slide_menu", "img_left", "img_right" };
    for (int i = 0; i < 3; ++i) home.carousel[i] = widget_lookup(win, carousel[i], 1);
    /* The carousel's own Coverflow and Shortcut cards; the lists' come first in the window. */
    widget_on(widget_lookup(home.carousel[0], "img_coverflow", 1), EVT_CLICK, coverflow_open, 0);
    widget_on(widget_lookup(home.carousel[0], "img_rockbox", 1), EVT_CLICK, home_shortcut_open, 0);
    coverflow_home_layout();
    if (theme()->pane_icon_x) {
        widget_on(win, EVT_WINDOW_FOREGROUND, home_foreground, 0);
        rearm(&home.count_timer, home_counts, PANE_BOOT_MS);
    }
    return result;
}

/* A library scan is running: its thread has started and not finished. Card apps (addon.c) wait for
 * it as Coverflow does, since the scan rewrites the records and directory lists they would read. */
int library_scanning(void) { return *(volatile int *)SCAN_THREAD && !*(volatile int *)SCAN_DONE; }
