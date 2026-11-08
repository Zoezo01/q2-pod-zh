/* Tidal cache (docs/internals.md#tidal-cache): each stream stock's Tidal thread plays is also
 * downloaded to the card, and a track already there plays from the card instead. */
#include "peq.h"

/* The file's extension from the URL's path, without the query: ".flac", ".mp4"; 0 without one. */
const char *tidal_ext(const char *url, char *out, unsigned n) {
    const char *q = strstr(url, "?"), *end = q ? q : url + strlen(url), *dot = end;
    while (dot > url && *dot != '.' && *dot != '/') --dot;
    if (*dot != '.' || end - dot >= (int)n || end - dot < 2) return 0;
    memcpy(out, dot, end - dot);
    out[end - dot] = 0;
    return out;
}

/* The cached file's name: "<key> <artist> - <title><ext>", the label left out when empty or cut to
 * fit n at a whole UTF-8 character, FAT's reserved characters as '_'. tidal_play finds it again by
 * key and ext, so the label only names it for Tidal Downloads and Now Playing. */
char *tidal_name(char *out, unsigned n, const char *key, const char *artist, const char *title,
                 const char *ext) {
    unsigned k = strlen(key), e = strlen(ext), i = 0;
    char label[512] = "";
    if (k + e >= n) return 0;
    if (title && *title)
        snprintf(label, sizeof label, artist && *artist ? " %s - %s" : " %s%s", artist && *artist ? artist : "", title);
    for (; label[i] && k + i + e + 1 < n; ++i)
        out[k + i] = (unsigned char)label[i] < 32 || strrchr("/\\:*?\"<>|", label[i]) ? '_' : label[i];
    while (i && ((unsigned char)label[i] & 0xc0) == 0x80) --i; /* cut before a split character */
    memcpy(out, key, k);
    memcpy(out + k + i, ext, e + 1);
    return out;
}

#ifndef PEQ_HOST
#include "offsets.inc"
#include "stock.h"

extern int stock_tidal_trampoline(const char *url);

#define TIDAL_STOP_MS 3000 /* stopped this long, the player was stopped, not between tracks */
#define TIDAL_FFMPEG "/usr/bin/ffmpeg"
#define TIDAL_COVER "/tmp/.tidal_cover.jpg" /* the running download's cover, the worker's alone */
#define TIDAL_TAG 200                       /* a tag's bytes, with "artist=" and its NUL */

static struct {
    unsigned lock[16]; /* a zeroed pthread mutex, 24 bytes on MIPS glibc */
    int busy, job;     /* a download thread runs; tidal_play's job waits in id, url and path */
    volatile unsigned gen, want, job_gen; /* tidal_play's count; the download's and the job's, from it */
    unsigned cleared; /* tidal_clear's count, under lock */
    unsigned long thread;
    char id[48], url[2048], path[296];
    /* the job's "title=", "artist=" and "album=" tags and cover file */
    char tags[3][TIDAL_TAG], cover[128];
} td __attribute__((section(".scratch")));

/* mcl_tidalStartPlayer's only caller, the stream-URL thread (0x486d88), keeps its own copy of the
 * song id it fetched in $s2 from 0x48647c until it frees it after the call, so a skip that has
 * already rewritten tidalsongid can't file one track's stream under another's id. */
__asm__(".globl tidal_start\ntidal_start:\nmove $a1, $s2\nj tidal_play\n");

/* Deletes the oldest finished downloads until the rest fit Streaming's Configuration cap (stream_cache,
 * 2 GB unless changed). stat is o32's: st_size word 12, st_mtime word 16. ponytail: a directory pass per
 * deletion, fine for a few hundred files. */
static void trim(void) {
    unsigned long long cap = (unsigned long long)(512u << stream_cache(0)) << 20;
    for (;;) {
        unsigned long long total = 0;
        long oldest = 0;
        char victim[300] = "", path[300];
        unsigned st[36];
        void *d = opendir(TIDAL_DIR);
        for (struct dirent *e; d && (e = readdir(d));) {
            if (e->d_name[0] == '.' || strstr(e->d_name, ".tmp")) continue;
            tk_snprintf(path, sizeof path, TIDAL_DIR "/%s", e->d_name);
            if (__xstat(3, path, st)) continue;
            total += st[12];
            if (!*victim || (long)st[16] < oldest) oldest = (long)st[16], tk_snprintf(victim, sizeof victim, "%s", path);
        }
        if (d) closedir(d);
        if (total <= cap || !*victim) return;
        if (unlink(victim)) return; /* a read-only card must not trap the download worker */
    }
}

static unsigned save(const void *data, unsigned size, unsigned n, void *file) {
    return fwrite(data, size, n, file) * size;
}

/* Stops the transfer once another track starts (tidal_play counts each) or the player has stayed
 * stopped for TIDAL_STOP_MS: a skip drops the copy within a second, a Stop within a few, as radio.c's
 * fetch_progress. ponytail: Stop is inferred from stock's play status, not signalled, so a stream that
 * sits stopped longer than that before it plays drops its copy too. Hook stock's stop if that bites. */
static int progress(void *stopped_at, long long a, long long b, long long c, long long d) {
    unsigned *at = stopped_at, now = now_ms(); /* low 32 bits: the difference below wraps safely */
    if (mclGetPlayStatus() != 1) /* 1 stopped */
        *at = 0;
    else if (!*at)
        *at = now | 1; /* 0 is not stopped */
    return td.gen != td.want || (*at && (int)(now - *at) >= TIDAL_STOP_MS);
}

/* tmp remuxed by stock ffmpeg with the queue row's tags and, into a FLAC, its cover: Tidal's files
 * carry none, so played from the card (Tidal Downloads) a copy showed Unknown Artist and no art.
 * On any failure tmp stays as downloaded. ponytail: no cover in an .m4a, as ffmpeg 4.2's mp4 muxer
 * writes none; the remux rewrites the whole file once more on the card. */
static void tag(const char *tmp, char (*tags)[TIDAL_TAG], int pic) {
    char out[sizeof td.path + 12];
    int flac = strstr(tmp, ".flac.tmp") != 0, status = -1;
    tk_snprintf(out, sizeof out, "%s.tag.tmp", tmp);
    int pid = fork();
    if (!pid) {
        child_oom_first();
        if (pic && flac)
            execl(TIDAL_FFMPEG, TIDAL_FFMPEG, "-nostdin", "-v", "quiet", "-i", tmp, "-i",
                  TIDAL_COVER, "-map", "0:a", "-map", "1:v", "-disposition:v", "attached_pic", "-c",
                  "copy", "-metadata", tags[0], "-metadata", tags[1], "-metadata", tags[2], "-f",
                  "flac", "-y", out, (char *)0);
        else
            execl(TIDAL_FFMPEG, TIDAL_FFMPEG, "-nostdin", "-v", "quiet", "-i", tmp, "-map", "0:a",
                  "-c", "copy", "-metadata", tags[0], "-metadata", tags[1], "-metadata", tags[2],
                  "-f", flac ? "flac" : "ipod", "-y", out, (char *)0);
        child_exit(127);
    }
    while (pid > 0 && waitpid(pid, &status, 0) < 0 &&
           *__errno_location() == 4) { /* EINTR: reap it */
    }
    if (!status && file_size(out) > 0 && !rename(out, tmp)) return;
    if (!access(out, 0)) unlink(out);
}

/* Downloads tidal_play's job, then the job a track started meanwhile left, until none waits. */
static void *download(void *unused) {
    char url[sizeof td.url], path[sizeof td.path], tmp[sizeof td.path + 4], tags[3][TIDAL_TAG],
        cover[sizeof td.cover];
    for (;;) {
        pthread_mutex_lock(td.lock);
        int job = td.job && td.job_gen == td.gen; /* a later track's start drops it */
        unsigned cleared = td.cleared;
        td.busy = job, td.job = 0;
        if (job) memcpy(url, td.url, sizeof url), memcpy(path, td.path, sizeof path), td.want = td.job_gen;
        if (job) memcpy(tags, td.tags, sizeof tags), memcpy(cover, td.cover, sizeof cover);
        pthread_mutex_unlock(td.lock);
        if (!job) return 0;
        /* now, while it's there: stock removes a page's covers as another opens */
        int pic = *cover == '/' && thumb(cover, TIDAL_COVER, 640, 640);
        tk_snprintf(tmp, sizeof tmp, "%s.tmp", path);
        unsigned stopped_at = 0;
        long code = 0;
        int ok = 0;
        void *f = fopen(tmp, "wb"), *c = f ? curl_easy_init() : 0;
        if (c) {
            curl_easy_setopt(c, 10002, url);         /* CURLOPT_URL */
            curl_easy_setopt(c, 20011, save);        /* WRITEFUNCTION */
            curl_easy_setopt(c, 10001, f);           /* WRITEDATA */
            curl_easy_setopt(c, 99, 1L);             /* NOSIGNAL: off the UI thread */
            curl_easy_setopt(c, 52, 1L);             /* FOLLOWLOCATION */
            curl_easy_setopt(c, 78, 15L);            /* CONNECTTIMEOUT */
            curl_easy_setopt(c, 19, 1024L);          /* LOW_SPEED_LIMIT: a stalled transfer ends, */
            curl_easy_setopt(c, 20, 30L);            /* LOW_SPEED_TIME   so busy always clears */
            curl_easy_setopt(c, 20219, progress);    /* XFERINFOFUNCTION */
            curl_easy_setopt(c, 10057, &stopped_at); /* XFERINFODATA */
            curl_easy_setopt(c, 43, 0L);             /* NOPROGRESS off, for it */
            /* Tidal's CDN roots are not in the firmware's bundle; hciplayer streams the same URL
             * unverified (ffmpeg's tls_verify=0), so the copy is trusted exactly as the stream is. */
            curl_easy_setopt(c, 64, 0L); /* SSL_VERIFYPEER */
            curl_easy_setopt(c, 81, 0L); /* SSL_VERIFYHOST */
            ok = !curl_easy_perform(c) && !curl_easy_getinfo(c, 0x200002, &code) && code == 200; /* RESPONSE_CODE */
            curl_easy_cleanup(c);
        }
        if (f) {
            ok = ok && !fflush(f) && !fsync(fileno(f)) && !ferror(f);
            ok = !fclose(f) && ok;
        }
        if (ok && tags[0][6]) tag(tmp, tags, pic); /* a title past "title=": a queue row named it */
        pthread_mutex_lock(td.lock); /* a Clear All since the job began drops it */
        ok = ok && cleared == td.cleared && !rename(tmp, path);
        pthread_mutex_unlock(td.lock);
        if (ok)
            trim();
        else
            unlink(tmp);
    }
}

/* key's cached file with ext in path (n bytes): "<key><ext>", or named as tidal_name names it. */
static int cached(const char *key, const char *ext, char *path, unsigned n) {
    unsigned k = strlen(key), e = strlen(ext), found = 0;
    void *d = opendir(TIDAL_DIR);
    for (struct dirent *f; d && !found && (f = readdir(d));) {
        unsigned len = strlen(f->d_name);
        found = len >= k + e && !memcmp(f->d_name, key, k) && (f->d_name[k] == ' ' || len == k + e) &&
                !strcmp(f->d_name + len - e, ext) && sizeof TIDAL_DIR + len < n;
        if (found) tk_snprintf(path, n, TIDAL_DIR "/%s", f->d_name);
    }
    if (d) closedir(d);
    return found;
}

/* Tidal Downloads' Clear All Downloads: every file in TIDAL_DIR, finished copies and .tmp ones;
 * .cache-size (a dot file, as trim and cached skip them) stays. There is no index: the list is the
 * folder. Under td.lock, with cleared counted: a download running or queued now renames nothing
 * into place after (download() checks cleared under the lock), and its .tmp is gone. A play after
 * caches as ever. ponytail: on the UI thread, holding the lock, a moment for a full 8 GB cache. */
void tidal_clear(void) {
    char path[300];
    pthread_mutex_lock(td.lock);
    ++td.cleared, td.job = 0, ++td.gen; /* gen: the running transfer stops (progress) */
    void *d = opendir(TIDAL_DIR);
    for (struct dirent *e; d && (e = readdir(d));)
        if (e->d_name[0] != '.') tk_snprintf(path, sizeof path, TIDAL_DIR "/%s", e->d_name), unlink(path);
    if (d) closedir(d);
    pthread_mutex_unlock(td.lock);
}

int tidal_play(const char *url, const char *id) {
    headset_manual();
    char ext[8], key[64], name[256], path[sizeof td.path];
    if (!id || strcmp(id, td.id)) ++td.gen; /* another track: cancels the download of the one before */
    int level = I(tidalStreamingLevelNow, 0), start = 0;
    if (!url || !id || !*id || strstr(id, "/") || strlen(id) > 40 || !tidal_ext(url, ext, sizeof ext))
        return stock_tidal_trampoline(url);
    /* AAC comes in an .mp4, which stock's file lists skip: kept as .m4a, so Tidal Downloads lists it. */
    if (!strcmp(ext, ".mp4")) memcpy(ext, ".m4a", 5);
    /* Keyed by quality too, so a higher Streaming quality never gets a lower one's copy. */
    tk_snprintf(key, sizeof key, "%s-%d", id, level);
    if (cached(key, ext, path, sizeof path)) return stock_tidal_trampoline(path);
    /* One download at a time: while a skipped track's is still ending, this one waits as the job its
     * loop takes next; while this track's own runs (a re-fetch of its URL), nothing changes. */
    pthread_mutex_lock(td.lock);
    if ((!td.busy || td.want != td.gen) && strlen(url) < sizeof td.url && (mkdir(TIDAL_DIR, 0755), card_space(TIDAL_DIR))) {
        /* The queue record this id came from (as get_tidal_trackurl finds it) names the file.
         * ponytail: read off the UI thread, as stock's URL thread reads it; copied at once. */
        const char *title = 0, *artist = 0, *album = 0, *cover = 0;
        void *q = P(mcl_pdeqplaylist, 0);
        for (unsigned i = 0, n = q ? deque_size(q) : 0; i < n && !title; ++i) {
            void *r = deque_at(q, i);
            if (r && P(r, REC_TIDAL_ID) && !strcmp(P(r, REC_TIDAL_ID), id))
                title = P(r, REC_TITLE) ? P(r, REC_TITLE) : "", artist = P(r, REC_ARTIST),
                album = P(r, REC_ALBUM), cover = P(r, REC_COVER);
        }
        tidal_name(name, sizeof name, key, artist, title, ext);
        tk_snprintf(td.tags[0], TIDAL_TAG, "title=%s", title ? title : "");
        tk_snprintf(td.tags[1], TIDAL_TAG, "artist=%s", artist ? artist : "");
        tk_snprintf(td.tags[2], TIDAL_TAG, "album=%s", album ? album : "");
        tk_snprintf(td.cover, sizeof td.cover, "%s", cover ? cover : "");
        tk_snprintf(td.id, sizeof td.id, "%s", id);
        tk_snprintf(td.url, sizeof td.url, "%s", url);
        tk_snprintf(td.path, sizeof td.path, TIDAL_DIR "/%s", name);
        td.job_gen = td.gen, td.job = 1;
        start = !td.busy, td.busy = 1;
    }
    pthread_mutex_unlock(td.lock);
    if (start && pthread_create(&td.thread, 0, download, 0))
        td.busy = td.job = 0;
    else if (start)
        pthread_detach(td.thread);
    return stock_tidal_trampoline(url);
}

/* Stock's stream thread (docs/internals.md#tidal-cache) and Baidu's both polled a flag every 10 ms
 * from power-on. Now each parks on a semaphore with no timer: only get_tidal_trackurl sets Tidal's
 * flag, and ringnav_tidal_track posts once it has, so the thread wakes per track asked for and parks
 * again after it. A post never gets lost (it counts), so one made mid-fetch only adds a pass.
 * Baidu's is never posted, so it parks for good. A zeroed sem_t is a semaphore at 0. */
static unsigned stream_gate[8], baidu_gate[8];

int ringnav_tidal_poll(unsigned us) { return sem_wait(stream_gate); }
int ringnav_baidu_poll(unsigned us) { return sem_wait(baidu_gate); }

/* Tidal track lists (docs/internals.md#tidal-track-lists): stock's playlist, album and favourite-tracks
 * threads each fetch one page of stTidalTracks (total, limit 50, offset, deque) into
 * /tmp/tidal_<thread>, which the page parses once the done flag is 2; the build drops their done
 * store, so each runs here once per page, parsed here, until the list is whole. The page then
 * parses an empty reply, so the last page isn't added twice. A page that adds nothing (a failed
 * request) or an offset moved under it (the page opened again, which resets it) ends the run; a
 * failed first page is left for the page's own parse, as stock. ponytail: TIDAL_MAX_TRACKS caps a
 * list at 40 requests, the wait before it shows; raise it if long lists load quickly enough. */
#define TIDAL_MAX_TRACKS 2000
extern int stock_tidalplaylist_trampoline(void *id);
extern int stock_tidalalbum_trampoline(void *id);
extern int stock_tidalfavs_trampoline(void *offset);

static void tidal_pages(int (*page)(void *), int (*parse)(const char *), void *arg, int offset, int by_offset) {
    int *t = (int *)stTidalTracks;
    char path[32];
    snprintf(path, sizeof path, "/tmp/tidal_%u", (unsigned)pthread_self());
    unsigned first = P(t, 12) ? deque_size(P(t, 12)) : 0;
    for (unsigned had = first;;) {
        unlink(path); /* a failed request must not leave the last page to parse again */
        page(by_offset ? (void *)(long)offset : arg);
        unsigned n = parse(path) > 0 && P(t, 12) ? deque_size(P(t, 12)) : 0;
        if (n <= had && had == first) return;
        if (n <= had || t[1] <= 0 || offset + t[1] >= t[0] || n >= TIDAL_MAX_TRACKS || t[2] != offset) {
            void *f = fopen(path, "w");
            if (f) fputs("{\"items\":[]}", f), fclose(f);
            return;
        }
        had = n, t[2] = offset += t[1];
    }
}

int ringnav_tidal_playlist(void *id) {
    tidal_pages(stock_tidalplaylist_trampoline, parseTidalPlaylistDetailJson, id, I(stTidalTracks, 8), 0);
    tidal_albuminfo_thread_run = 2;
    return 0;
}
int ringnav_tidal_album(void *id) {
    tidal_pages(stock_tidalalbum_trampoline, parseTidalAlbumDetailJson, id, I(stTidalTracks, 8), 0);
    tidal_albuminfo_thread_run = 2;
    return 0;
}
/* My Collection's Tracks: stock pages these on a touch scroll to the list's end, which the wheel
 * evidently doesn't reach (inferred from #101, not traced); after the whole list stTidalTracks' offset is the last page's, so that check finds no more. */
int ringnav_tidal_favs(void *offset) {
    tidal_pages(stock_tidalfavs_trampoline, parseTidalColTracksJson, 0, (int)(long)offset, 1);
    tidal_collection_thread_run = 2;
    return 0;
}

extern int stock_tidaltrack_trampoline(void *a, void *b, void *c, void *d);
/* get_tidal_trackurl: any Tidal track asked for (a queue restored at power-on, a pick in Now
 * Playing) wakes the stream thread, after stock has set its flag (0x483278). */
int ringnav_tidal_track(void *a, void *b, void *c, void *d) {
    int r = stock_tidaltrack_trampoline(a, b, c, d);
    sem_post(stream_gate);
    return r;
}

/* Smart Wi-Fi (smart_wifi.c smart_needed): a download in progress keeps the radio on. */
int tidal_busy(void) { return td.busy; }
#endif
