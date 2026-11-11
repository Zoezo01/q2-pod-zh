/* Playlist art (docs/internals.md#playlist-art): a playlist's own picture,
 * _explaylist_data/<name>.jpg or .png, else a 2x2 of four of its albums' covers, as files in Cover
 * Flow's cache. A file's name holds a hash of what it shows (the picture's name, size and time; the
 * songs' files in id order), so changed songs or a new picture get a new name and the image cache
 * never shows stale art; a name is only given to a whole file. One worker pthread builds them, a
 * playlist at a time, touching only files, its own SQLite connection and Cover Flow's art calls. */
#include "playlist_art.h"
#include "coverflow.h"

#define PL_DIR PEQ_ROOT "/mnt/mmc/_explaylist_data"
#define PL_SMALL 52  /* stock's playlist row image */
#define PL_JOBS 128  /* queued between two idle moments; playlists are ids 0 to 100 */
#define PL_TRIES 16  /* covers built for one 2x2 at most */
#define PL_LISTS 128 /* playlists hashed; they are ids 0 to 100 */
/* ponytail: the 2x2 picks from a playlist's first 64 albums by id, which bounds the dedup and the
 * heap; a playlist whose first 64 albums have no art shows none */
#define PL_ALBUMS 64
/* The reads go PL_PAGE rows at a time from the last id read (rowid order: no sort, no index), a
 * statement each, so SQLite's shared lock is held for one page and a writer waits for one at most.
 * Each statement's first column is the id. */
#define PL_PAGE 1024
#define PL_HASHES "select id,cd_num,fileurl from playlist where id>%u order by id limit 1024"
#define PL_SONGS                                                                                   \
    "select id,fileurl,album,songer,albumsonger from playlist where id>%u and cd_num=%d order by " \
    "id limit 1024"
/* the user's own picture, decoded by ffmpeg, at most this many pixels (a 12 MP phone photo); a
 * bigger one, or one whose size its header doesn't tell, shows the 2x2 */
#define PL_PICTURE_PX 12000000
/* Each cover filled to 80x80 and cut square, so none is squashed; hstack and vstack, as ffmpeg 4.2
 * has no xstack. */
#define PL_FILL(i, o) "[" #i "]scale=80:80:force_original_aspect_ratio=increase,crop=80:80[" #o "];"
#define PL_STACK "[a][b]hstack[t];[c][d]hstack[u];[t][u]vstack"
#define PL_TILE PL_FILL(0, a) PL_FILL(1, b) PL_FILL(2, c) PL_FILL(3, d) PL_STACK

typedef struct {
    char *url;
    unsigned id, key; /* the album as album_cmp tells it apart; Cover Flow's key for its art */
} pl_album_t;
typedef struct {
    unsigned hash;
    int collect; /* the worker's: each album's first song too */
    unsigned n;
    pl_album_t *albums;
    unsigned songs;
} pl_songs_t;
typedef struct {
    char *name;
    int cd_num, made;
    unsigned asked;       /* the UI's hash for it when queued */
    int read;             /* the worker read its songs: */
    unsigned hash, songs; /* their hash and count */
} pl_job_t;
typedef struct {
    struct {
        int cd_num;
        unsigned hash, songs;
    } *at; /* PL_LISTS: a refresh in id order, pl_note appends; 0 until one succeeds */
    int n;
} pl_lists_t;

void (*playlist_art_ready)(int cd_num) __attribute__((section(".scratch")));
static struct {
    pl_job_t *jobs;        /* PL_JOBS: the UI appends at total, the worker takes them in order */
    int total, done, told; /* told: the UI's, the jobs it reported */
    int idle;              /* the worker found none left, and ends */
    int running;
    volatile int cancel;
    unsigned long thread;
    unsigned timer;
    pl_lists_t lists;  /* the UI's: each playlist's songs hash, from the worker's read */
    int want_lists;    /* the UI asks the worker for a read of every playlist's songs */
    pl_lists_t *fresh; /* the worker's read, whole, for the poll to take */
} pl __attribute__((section(".scratch")));

/* A song: the hash over its fileurl, and for the worker its album, once. Not its id, which an
 * import of the same songs again (stock's, a sync's) renews. */
static int song_row(void *ctx, int n, char **v, char **c) {
    pl_songs_t *s = ctx;
    if (pl.cancel) return 1; /* a join waits: the read stops, read 0, nothing named or marked */
    if (n < 2) return 0;
    ++s->songs;
    s->hash = fnv(s->hash, (const unsigned char *)v[1]);
    if (!s->collect || n < 5 || !v[1] || s->n == PL_ALBUMS) return 0;
    unsigned id = album_ident(v[2], v[4], v[1]);
    for (unsigned i = 0; i < s->n; ++i)
        if (s->albums[i].id == id) return 0;
    pl_album_t *a = s->n & 15 ? s->albums : realloc(s->albums, (s->n + 16) * sizeof *a);
    char *url = a ? strdup(v[1]) : 0;
    if (a) s->albums = a;
    if (!url) return 1; /* out of memory: the query stops, and no art comes of part of it */
    a[s->n++] = (pl_album_t){ url, id, tags_key(v[3], v[2]) };
    return 0;
}

/* A song of every playlist's (id, cd_num, fileurl), in id order (a rowid scan, no sort), so each
 * playlist's own songs come in the order song_row reads them: its hash, a playlist each, the last
 * one's entry tried first. */
static int list_row(void *ctx, int n, char **v, char **c) {
    pl_lists_t *t = ctx;
    static int last;
    if (pl.cancel) return 1; /* a join waits: the read fails, nothing published */
    if (n < 3 || !v[1]) return 0;
    int cd = atoi(v[1]);
    if (cd < 0 || cd > 100) return 0; /* junk (-1, 999): no playlist's, and no slot */
    int i = last < t->n && t->at[last].cd_num == cd ? last : 0;
    while (i < t->n && t->at[i].cd_num != cd) ++i;
    if (i == t->n) {
        if (t->n == PL_LISTS) return 0; /* the rest left out, their art not shown */
        t->at[t->n].cd_num = cd, t->at[t->n++].hash = FNV_SEED;
    }
    ++t->at[last = i].songs;
    t->at[i].hash = fnv(t->at[i].hash, (const unsigned char *)v[2]);
    return 0;
}

/* A page's row: its id (the first column) kept for the next page, then row's. */
typedef struct {
    int (*row)(void *, int, char **, char **);
    void *ctx;
    unsigned last, rows;
} pl_page_t;
static int page_row(void *ctx, int n, char **v, char **c) {
    pl_page_t *p = ctx;
    ++p->rows;
    if (n && v[0]) p->last = (unsigned)atoi(v[0]);
    return p->row(p->ctx, n, v, c);
}
/* sql (PL_HASHES, PL_SONGS) through row, a page at a time, on a connection of its own (era_sql's
 * way: the device's libsqlite3 is THREADSAFE=1); 0 when a page fails or a join waits. Between pages
 * it holds no lock, so a writer commits there; one committing meanwhile (its PENDING lock refuses
 * the next page) fails the read, which steps aside: the next rebuild reads again. */
static int query(const char *sql, int cd_num, int (*row)(void *, int, char **, char **),
                 void *ctx) {
    char page[sizeof PL_SONGS + 32];
    void *db = 0;
    pl_page_t p = { row, ctx, 100, PL_PAGE };
    int bad = sqlite3_open("/mnt/data/database.db", &db) ||
              sqlite3_exec(db, "PRAGMA cache_size=64", 0, 0, 0); /* era_sql's: a heap of KBs */
    while (!bad && p.rows == PL_PAGE) {
        tk_snprintf(page, sizeof page, sql, p.last, cd_num);
        p.rows = 0;
        bad = pl.cancel || sqlite3_exec(db, page, page_row, &p, 0);
    }
    if (db) sqlite3_close(db);
    return !bad;
}

/* cd_num's songs through song_row.
 * ponytail: each job reads the table again for its own cd_num (no index), so a first build of N
 * playlists reads it N + 1 times; collecting each playlist's first PL_ALBUMS albums in the
 * refresh's one pass would make it one. */
static int songs(int cd_num, pl_songs_t *s) { return query(PL_SONGS, cd_num, song_row, s); }

/* The worker's: every playlist's songs hash, in one read, published whole for the poll (one not
 * taken yet is replaced); a failed read publishes none, so the UI keeps the table it has. */
static void pl_lists(void) {
    pl_lists_t *t = calloc(1, sizeof *t);
    if (t && (t->at = calloc(PL_LISTS, sizeof *t->at)) && query(PL_HASHES, 0, list_row, t))
        t = __atomic_exchange_n(&pl.fresh, t, __ATOMIC_ACQ_REL);
    if (t) free(t->at), free(t);
}

/* cd_num's place in the UI's table, or its count when it has none (no songs, or no refresh yet) */
static int pl_entry(int cd_num) {
    int i = 0;
    while (i < pl.lists.n && pl.lists.at[i].cd_num != cd_num) ++i;
    return i;
}

/* cd_num's songs as the last refresh, or a build since, counted them */
unsigned playlist_songs(int cd_num) {
    int i = pl_entry(cd_num);
    return i < pl.lists.n ? pl.lists.at[i].songs : 0;
}

/* name as a file name: the characters FAT refuses become _ */
static void fat_name(char *out, unsigned size, const char *name) {
    unsigned i = 0;
    for (; name && name[i] && i + 1 < size; ++i)
        out[i] = (unsigned char)name[i] < 32 || strrchr("\\/:*?\"<>|", name[i]) ? '_' : name[i];
    if (size) out[i] = 0;
}

/* The playlist's own picture in src (600 bytes), and a hash of its name, size and time; 0: none.
 * stat is o32's: st_mode word 5, st_size word 12, st_mtime word 16. */
static unsigned picture(const char *name, char *src) {
    static const char *const ext[] = { ".jpg", ".png" };
    char fat[256];
    unsigned st[36];
    fat_name(fat, sizeof fat, name);
    for (unsigned i = 0; i < 2; ++i) {
        tk_snprintf(src, 600, PL_DIR "/%s%s", fat, ext[i]);
        if (__xstat(3, src, st) || (st[5] & 0170000) != 0100000) continue;
        unsigned h = hash_bytes(fnv(FNV_SEED, (const unsigned char *)src),
                                (const unsigned char *)&st[12], 4);
        h = hash_bytes(h, (const unsigned char *)&st[16], 4);
        return h | !h;
    }
    return 0;
}

/* ART_DIR/<kind>_<cd_num>_<hash>, the stem of a playlist's files (out: 512 bytes) */
static char *stem(char *out, const char *kind, int cd_num, unsigned hash) {
    tk_snprintf(out, 512, ART_DIR "/%s_%d_%08x", kind, cd_num, hash);
    return out;
}
static char *named(char *out, const char *at, const char *suffix) {
    tk_snprintf(out, 540, "%s%s", at, suffix);
    return out;
}

/* The 2x2's cell i (left to right, top to bottom) of k covers: a b / b a, a b / c a, a b / c d. */
static unsigned quad(unsigned k, unsigned i) {
    static const unsigned char cells[4][4] = {
        { 0, 0, 0, 0 }, { 0, 1, 1, 0 }, { 0, 1, 2, 0 }, { 0, 1, 2, 3 }
    };
    return cells[(k - 1) & 3][i & 3];
}

/* at.tmp.jpg, whole, as at.jpg, its small copy at_s.jpg named first, so at.jpg means both. */
static int publish(const char *at) {
    char big[540], tmp[540], small[540], stmp[540];
    named(tmp, at, ".tmp.jpg"), named(stmp, at, "_s.tmp.jpg");
    int ok = thumb(tmp, stmp, PL_SMALL, PL_SMALL) && !rename(stmp, named(small, at, "_s.jpg"));
    if (ok && !rename(tmp, named(big, at, ".jpg"))) return 1;
    if (ok) unlink(small);
    unlink(tmp), unlink(stmp);
    return 0;
}

/* An empty at.jpg: no art from it, so it is not built again until its hash changes. */
static void mark(const char *at) {
    char path[540];
    void *f = fopen(named(path, at, ".jpg"), "w");
    if (f) fclose(f);
}

/* The 2x2 of k 160 px covers into at.tmp.jpg; one cover alone, whole. */
static int tile(char (*cover)[540], unsigned k, const char *at) {
    char tmp[540], ff[548];
    if (k == 1) return thumb(cover[0], named(tmp, at, ".tmp.jpg"), ART_SIZE, ART_SIZE);
    const char *in[4] = { cover[quad(k, 0)], cover[quad(k, 1)], cover[quad(k, 2)],
                          cover[quad(k, 3)] };
    tk_snprintf(ff, sizeof ff, "%s.ff", named(tmp, at, ".tmp.jpg"));
    int exited, pid = fork();
    if (!pid) {
        child_oom_first();
        execl(ART_FFMPEG, ART_FFMPEG, "-nostdin", "-v", "quiet", "-xerror", "-i", in[0], "-i",
              in[1], "-i", in[2], "-i", in[3], "-filter_complex", PL_TILE, "-frames:v", "1",
              "-pix_fmt", "yuvj420p", "-q:v", "3", "-f", "mjpeg", "-y", ff, (char *)0);
        child_exit(127);
    }
    return ff_wait(pid, ff, tmp, &exited);
}

/* The 2x2 of s's albums, named at: the first four with art in song id order, so songs added after
 * them leave the picture as it is; each album's Cover Flow art when built, else its song's art
 * found as Cover Flow's build finds it (as Now Playing does, its folder's cover.jpg taken even when
 * other albums share the folder). None with art, or a 2x2 that failed: marked, unless the card went
 * or an ffmpeg was killed. Albums Cover Flow found none for are not tried: each could cost an
 * ffmpeg. ponytail: PL_TRIES builds, so a big playlist without art costs a bounded wait; a shared
 * folder's cover.jpg Cover Flow refused is not looked for. */
/* ponytail: an album Cover Flow hasn't built is decoded in demo (album_art, toolsGetAlbumCover
 * under stock's art locks), so a bad embedded cover can take demo down, once per album (the note
 * and strike). Building those with ffmpeg alone (ff_thumb, then beside_art(..., ff_thumb)) would
 * keep them out of demo, let the worker hold no stock lock, and delete PL_BUILDING, the shared note
 * and strike, the album_art split and playlist_art_clean_end, for one or two processes per such
 * album. */
static int four(pl_songs_t *s, const char *at, unsigned kills) {
    char cover[4][540];
    unsigned k = 0, tries = 0, temp = 0;
    for (unsigned i = 0; i < s->n && k < 4 && !pl.cancel; ++i) {
        const pl_album_t *a = &s->albums[i];
        long n = file_size(art_path(cover[k], a->key, ""));
        if (n > 0) {
            ++k;
            continue;
        }
        if (!n || access(a->url, 0)) continue; /* Cover Flow found none; gone: no ffmpeg, no try */
        if (tries++ == PL_TRIES) break;
        tk_snprintf(cover[k], 540, "%s_%u.tmp.jpg", at, k);
        art_note(PL_BUILDING, a->key, a->url); /* an album that kills demo is struck, not retried */
        int got = !pl.cancel && album_art(a->url, cover[k], 0); /* a clean end since: not begun */
        unlink(PL_BUILDING);
        if (got)
            temp |= 1u << k++;
        else if (unlink(cover[k]), pl.cancel || access(ART_DIR, 0))
            break; /* stopped, or the card is gone */
    }
    /* stopped (the page closed, a join waits): nothing named, no marker */
    int made = k && !pl.cancel && tile(cover, k, at) && publish(at);
    if (!made && !pl.cancel && kills == ff_kills && !access(ART_DIR, 0)) mark(at);
    for (unsigned i = 0; i < 4; ++i)
        if (temp & 1u << i) unlink(cover[i]);
    return made;
}

/* One playlist's art; 1 when a file was made. Its picture first; one over PL_PICTURE_PX, of a size
 * its header doesn't tell, or that won't decode, is marked and its 2x2 shown until the picture
 * changes; one whose ffmpeg is killed (memory short just then) is tried again at the next ask. The
 * 2x2 is named by the songs this read, so songs changed meanwhile never show it: their own hash
 * names a file still to build. */
static int pl_build(pl_job_t *j) {
    char src[600], at[512], path[540];
    if (access(ART_DIR, 0) || !card_space(ART_DIR)) return 0;
    unsigned kills = ff_kills, pic = picture(j->name, src);
    long n = pic ? file_size(named(path, stem(at, "plc", j->cd_num, pic), ".jpg")) : 0;
    if (n > 0) return 0;
    int w = 0, h = 0;
    if (n < 0 && (!picture_size(src, &w, &h) || !w || !h || (long long)w * h > PL_PICTURE_PX)) {
        if (access(src, 4)) return 0; /* gone meanwhile */
        mark(at);
    } else if (n < 0) {
        /* by ffmpeg alone: any file the user drops there, which stock's decoder in demo could die
         * on at every open */
        if (ff_thumb(src, named(path, at, ".tmp.jpg"), ART_SIZE, ART_SIZE) && !pl.cancel &&
            publish(at))
            return 1;
        unlink(path);
        if (pl.cancel || access(src, 4) || access(ART_DIR, 0) || kills != ff_kills)
            return 0; /* stopped, gone, or ffmpeg killed */
        mark(at);
    }
    pl_songs_t s = { FNV_SEED, 1, 0, 0, 0 };
    int read = !pl.cancel && songs(j->cd_num, &s);
    int made = read && file_size(named(path, stem(at, "pl", j->cd_num, s.hash), ".jpg")) < 0 &&
               four(&s, at, kills);
    j->hash = s.hash, j->songs = s.songs, j->read = read;
    for (unsigned i = 0; i < s.n; ++i) free(s.albums[i].url);
    free(s.albums);
    return made;
}

static void *pl_worker(void *unused) {
    thread_lower(0);         /* below the player and the UI, as the ffmpeg runs it forks */
    art_strike(PL_BUILDING); /* the album the last build died on, demo with it */
    for (int i;;) {
        if (__atomic_exchange_n(&pl.want_lists, 0, __ATOMIC_ACQUIRE)) pl_lists();
        if (pl.cancel || (i = pl.done) >= __atomic_load_n(&pl.total, __ATOMIC_ACQUIRE)) break;
        pl.jobs[i].made = pl_build(&pl.jobs[i]);
        if (access(ART_DIR, 0) || !card_space(ART_DIR)) pl.cancel = 1; /* the card gone or full */
        __atomic_store_n(&pl.done, i + 1, __ATOMIC_RELEASE);
    }
    __atomic_store_n(&pl.idle, 1, __ATOMIC_RELEASE);
    return 0;
}

static int pl_tick(const void *unused);
static void pl_start(void) {
    /* none while USB Storage has, or is taking, the card (ringnav_usbmode stopped the last) */
    if (navigator_window_is_exist("usbmode_page")) return;
    mkdir(ART_DIR, 0755);
    pl.idle = 0;
    if (pthread_create(&pl.thread, 0, pl_worker, 0)) return;
    pl.running = 1;
    if (!pl.timer) pl.timer = timer_add(pl_tick, 0, 250);
}

static void pl_reset(void) {
    for (int i = 0; i < pl.total; ++i) free(pl.jobs[i].name);
    pl.total = pl.done = pl.told = pl.cancel = 0;
}

/* A finished job's songs, as its worker read them, into the UI's table, in place of a read of
 * every playlist's on the UI thread; not when a refresh since it was queued has newer ones. */
static void pl_note(const pl_job_t *j) {
    int i = pl_entry(j->cd_num);
    if (!j->read || !pl.lists.at || i == PL_LISTS ||
        (i < pl.lists.n ? pl.lists.at[i].hash : FNV_SEED) != j->asked)
        return;
    if (i == pl.lists.n) ++pl.lists.n, pl.lists.at[i].cd_num = j->cd_num;
    pl.lists.at[i].hash = j->hash, pl.lists.at[i].songs = j->songs;
}

/* The worker's read of every playlist's songs in place of the UI's table; each playlist it changed
 * (new, gone, or its hash or count another) is told, as its rows ask again. */
static void pl_take(pl_lists_t *t) {
    int changed[2 * PL_LISTS], k = 0;
    for (int i = 0; i < t->n; ++i) {
        int j = pl_entry(t->at[i].cd_num);
        if (j == pl.lists.n || pl.lists.at[j].hash != t->at[i].hash ||
            pl.lists.at[j].songs != t->at[i].songs)
            changed[k++] = t->at[i].cd_num;
    }
    for (int j = 0; j < pl.lists.n; ++j) {
        int i = 0;
        while (i < t->n && t->at[i].cd_num != pl.lists.at[j].cd_num) ++i;
        if (i == t->n) changed[k++] = pl.lists.at[j].cd_num;
    }
    free(pl.lists.at);
    pl.lists = *t;
    free(t);
    for (int i = 0; i < k && playlist_art_ready; ++i) playlist_art_ready(changed[i]);
}

/* The worker joined once it found nothing left to do; a read asked or jobs queued as it ended
 * start another. */
static void playlist_art_poll(void) {
    if (pl.running && __atomic_load_n(&pl.idle, __ATOMIC_ACQUIRE)) {
        pthread_join(pl.thread, 0);
        pl.running = 0;
    }
    pl_lists_t *t = __atomic_exchange_n(&pl.fresh, 0, __ATOMIC_ACQUIRE);
    if (t) pl_take(t);
    int done = __atomic_load_n(&pl.done, __ATOMIC_ACQUIRE);
    while (pl.told < done) {
        const pl_job_t *j = &pl.jobs[pl.told++];
        pl_note(j);
        if (j->made && playlist_art_ready) playlist_art_ready(j->cd_num);
    }
    if (pl.running) return;
    if ((pl.done < pl.total || pl.want_lists) && !pl.cancel)
        pl_start();
    else
        pl_reset();
}

/* Every playlist's songs read again, by the worker: once per Playlists rebuild, no SQLite here. */
void playlist_art_refresh(void) {
    __atomic_store_n(&pl.want_lists, 1, __ATOMIC_RELEASE);
    if (!pl.running) pl_start();
}

static int pl_tick(const void *unused) {
    pl.timer = 0;
    if (pl.running) reset_poweroptions_timer(1, 1, 0); /* standby, auto power-off: not mid-build */
    playlist_art_poll();
    if (pl.running && !pl.timer) pl.timer = timer_add(pl_tick, 0, 250);
    return 0; /* RET_REMOVE */
}

void playlist_art_stop(void) {
    worker_stop(pl.thread, &pl.running, &pl.cancel, &pl.timer);
    pl_reset();
}

/* A clean end (coverflow_clean_end): the worker stops after the cover it is on, beginning no other,
 * so no note outlives demo to strike an album the kill that follows interrupts, and this one's
 * goes. */
void playlist_art_clean_end(void) {
    pl.cancel = 1;
    unlink(PL_BUILDING);
}

/* cd_num queued once (one being built counts: a change meanwhile waits for the next ask). */
static void pl_queue(int cd_num, const char *name, unsigned asked) {
    int i = __atomic_load_n(&pl.done, __ATOMIC_ACQUIRE);
    while (i < pl.total && pl.jobs[i].cd_num != cd_num) ++i;
    if (i == pl.total && i < PL_JOBS && (pl.jobs || (pl.jobs = calloc(PL_JOBS, sizeof *pl.jobs))) &&
        (pl.jobs[i].name = strdup(name ? name : ""))) {
        pl.jobs[i].cd_num = cd_num, pl.jobs[i].made = pl.jobs[i].read = 0;
        pl.jobs[i].asked = asked;
        __atomic_store_n(&pl.total, i + 1, __ATOMIC_RELEASE);
    }
    if (!pl.running && pl.done < pl.total) pl_start();
}

/* Its picture's file when that decoded, else the 2x2 of its songs as the last refresh read them
 * (none before the first read: the poll that brings it asks again); a marker (empty) is none. No
 * SQLite: the UI thread asks per row. */
int playlist_art(int cd_num, const char *name, int small, char *url, int size) {
    char src[600], at[512], path[540];
    unsigned pic = picture(name, src), hash = FNV_SEED; /* no songs: the empty playlist's */
    long n = pic ? file_size(named(path, stem(at, "plc", cd_num, pic), ".jpg")) : 0;
    int i = pl_entry(cd_num);
    if (i < pl.lists.n) hash = pl.lists.at[i].hash;
    if (!n && pl.lists.at) n = file_size(named(path, stem(at, "pl", cd_num, hash), ".jpg"));
    if (n > 0) {
        tk_snprintf(url, size, "file://%s", named(path, at, small ? "_s.jpg" : ".jpg"));
        return 1;
    }
    if (n < 0) pl_queue(cd_num, name, hash);
    return 0;
}
