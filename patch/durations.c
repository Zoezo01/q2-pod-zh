/* Shared song durations, measured when Long Songs opens or, with Scan Song Durations on, after
 * Update Local Music. The UI reads the library's paths and seeds each job from the cache; the
 * worker measures, builds the next cache from its jobs and saves it; the UI swaps it in after the
 * join. No TagLib or stat in a duration lookup. See docs/internals.md, "Song durations". */
#include "navigation.h"

#define DUR_FILE "/mnt/data/ringnav-durations-v1"
#define DUR_MAGIC "Q2DUR2\0" /* and its NUL: 8 bytes, then the records */
/* ponytail: a library over DUR_SONGS songs gets no pass (the scan has no song limit since V1.36);
 * raise it with DUR_MAX's read if a library that big appears */
#define DUR_SONGS 100000u
#define DUR_MAX (8u + DUR_SONGS * sizeof(rec_t))
#define DUR_WAIT_MS 5000 /* a clean end's wait for the current file */
typedef struct {
    unsigned long long key; /* the path's FNV-1a 64 */
    unsigned size, mtime, ns;
    int sec; /* 0: tried, no length; -1 (a job's alone): unknown */
} rec_t;
typedef struct {
    char *path;
    rec_t r;
} job_t;
static struct {
    /* the cache as read (magic, then n records sorted by key); the worker's next */
    char *blob, *out;
    job_t *jobs;
    unsigned n, out_n, total, cap, writable;
    unsigned pass, whole; /* library_gen + 1 of the jobs' library; of the last complete pass */
    unsigned long thread;
    int running, done, cancel, exited, due; /* due: a scan ended, set on its thread */
} ds __attribute__((section(".scratch")));

static unsigned long long duration_key(const char *path) {
    unsigned long long h = 0xcbf29ce484222325ull;
    while (*path) h = (h ^ (unsigned char)*path++) * 0x100000001b3ull;
    return h;
}
static rec_t *duration_find(unsigned long long key) {
    rec_t *tab = (rec_t *)(ds.blob + 8);
    unsigned lo = 0, hi = ds.n;
    while (lo < hi) {
        unsigned mid = lo + (hi - lo) / 2;
        if (tab[mid].key < key)
            lo = mid + 1;
        else
            hi = mid;
    }
    return lo < ds.n && tab[lo].key == key ? tab + lo : 0;
}

/* The file is the table: magic, then records ascending by key. Anything else is no cache. The old
 * hash-only ringnav-lengths stays untouched: it has no stat signature to check. */
static void duration_read(void) {
    if (ds.writable || ds.blob || ds.running) return; /* the worker reads n and writable */
    unsigned size = 0;
    char *data = read_all(DUR_FILE, DUR_MAX, &size);
    ds.writable = data || file_size(DUR_FILE) <= 0;
    if (!data) return;
    rec_t *tab = (rec_t *)(data + 8);
    unsigned n = size >= 8 ? (size - 8) / sizeof *tab : 0;
    int ok = size >= 8 && !memcmp(data, DUR_MAGIC, 8) && (size - 8) % sizeof *tab == 0;
    for (unsigned i = 0; ok && i < n; ++i)
        ok = tab[i].sec >= 0 && (!i || tab[i - 1].key < tab[i].key);
    if (ok)
        ds.blob = data, ds.n = n;
    else
        free(data);
}

int duration_seconds(void *song) {
    if (!song) return -1;
    int start = I(song, REC_CUE_START), end = I(song, REC_CUE_END);
    if (start < 0) start = 0;
    if (end > start) return end - start;
    const char *path = P(song, REC_PATH);
    if (!path) return -1;
    duration_read();
    rec_t *entry = duration_find(duration_key(path));
    /* ponytail: two paths sharing a 64-bit key (~1e-10 at 47k songs) share the first's length */
    return !entry ? -1 : entry->sec > start ? entry->sec - start : 0;
}
/* toolsTimeItoa's 3:45, 1:02:05 from an hour, as Now Playing's (tools/ipod.py rewrites stock's
 * padded formats; size: at least 9); empty, not 0:00, for an unknown or unreadable file, or with
 * Scan Song Durations off: Long Songs' own measures stay its own. */
const char *duration_text(char *text, unsigned size, void *song) {
    int sec = st.scan_durations ? duration_seconds(song) : 0;
    if (size) text[0] = 0;
    if (sec > 0) toolsTimeItoa(text, sec);
    return text;
}

/* ponytail: a signature, not a checksum: a file replaced by one of the same size (mod 4 GiB) and
 * mtime keeps the old length, and a filesystem's coarse mtime hides a quick rewrite. */
static int duration_stat(const char *path, unsigned *stamp) {
    unsigned sx[64]; /* struct statx: stx_mode's half at byte 28, stx_size 40, stx_mtime 112 */
    if (ringnav_statx(-100, path, 0, 0x241 /* TYPE, MTIME, SIZE */, sx) ||
        (sx[7] & 0170000) != 0100000)
        return 0;
    stamp[0] = sx[10], stamp[1] = sx[28], stamp[2] = sx[30];
    return 1;
}
/* job measured again when its file's signature isn't its seed's; 1 when that changed it. A file
 * that can't be statted, or changes while TagLib reads it, keeps its seed for the next pass. */
static int duration_probe(job_t *job) {
    unsigned before[3], after[3];
    if (!duration_stat(job->path, before) ||
        (job->r.sec >= 0 && !memcmp(before, &job->r.size, sizeof before)))
        return 0;
    /* ponytail: an oversized Ogg header (ogg_tags) would cost TagLib ~4 times it; no length */
    void *f = ogg_tags(job->path, 0) ? 0 : taglib_file_new(job->path, 1);
    void *p = f ? taglib_file_audioproperties(f) : (void *)0;
    int sec = p ? ((int (*)(void *))(*(void ***)p)[3])(p) : 0; /* AudioProperties::length() */
    if (f) taglib_file_free(f);
    if (!duration_stat(job->path, after) || memcmp(before, after, sizeof before)) return 0;
    memcpy(&job->r.size, before, sizeof before);
    job->r.sec = sec > 0 ? sec : 0;
    return 1;
}
static int job_order(const void *a, const void *b) {
    const job_t *x = a, *y = b;
    return x->r.key != y->r.key ? (x->r.key > y->r.key) - (x->r.key < y->r.key)
                                : strcmp(x->path, y->path);
}
/* The jobs are the library in its scope: the next cache is every one with a length, a job not
 * reached keeping its seed, so any pass, cancelled or complete, drops songs outside them. Saved
 * here, off the UI thread, when it differs. */
static void *duration_worker(void *unused) {
    thread_lower(1);
    job_t *jobs = ds.jobs;
    unsigned total = ds.total, n = 0;
    int changed = 0;
    qsort(jobs, total, sizeof *jobs, job_order);
    for (unsigned i = 0; i < total && !__atomic_load_n(&ds.cancel, __ATOMIC_ACQUIRE); ++i) {
        if (i && !strcmp(jobs[i - 1].path, jobs[i].path))
            jobs[i].r = jobs[i - 1].r; /* CUE tracks of one file: measured once */
        else
            changed |= duration_probe(jobs + i);
        __atomic_store_n(&ds.done, (int)i + 1, __ATOMIC_RELEASE);
    }
    char *out = calloc(8 + total * sizeof(rec_t), 1);
    if (out) {
        rec_t *tab = (rec_t *)(out + 8);
        memcpy(out, DUR_MAGIC, 8);
        for (unsigned i = 0; i < total; ++i)
            if (jobs[i].r.sec >= 0 && (!n || tab[n - 1].key != jobs[i].r.key)) tab[n++] = jobs[i].r;
        if ((changed || n != ds.n) && ds.writable &&
            !blob_io(DUR_FILE, DUR_FILE ".tmp", out, 8 + n * sizeof *tab, 1))
            unlink(DUR_FILE ".tmp");
        ds.out = out, ds.out_n = n;
    }
    __atomic_store_n(&ds.exited, 1, __ATOMIC_RELEASE);
    return 0;
}

static void duration_jobs_free(void) {
    for (unsigned i = 0; i < ds.total; ++i) free(ds.jobs[i].path);
    free(ds.jobs);
    ds.jobs = 0;
    ds.total = ds.cap = 0;
}
/* A job for path, seeded with what the cache has; 0 when there is no room. UI thread. */
static int duration_job(const char *path) {
    if (ds.total >= DUR_SONGS) return 0; /* its cache would be over DUR_MAX, refused at boot */
    if (!path || path[0] != '/' || strlen(path) >= 4096) return 1;
    if (ds.total == ds.cap) {
        unsigned cap = ds.cap ? ds.cap * 2 : 1024;
        job_t *grown = realloc(ds.jobs, cap * sizeof(job_t));
        if (!grown) return 0;
        ds.jobs = grown, ds.cap = cap;
    }
    job_t *job = ds.jobs + ds.total;
    unsigned long long key = duration_key(path);
    rec_t *old = duration_find(key);
    job->r = old ? *old : (rec_t){ key, 0, 0, 0, -1 };
    if (!(job->path = strdup(path))) return 0;
    ++ds.total;
    return 1;
}
/* Any job its cache has no length for: a song new to it. */
static int duration_unknown(void) {
    for (unsigned i = 0; i < ds.total; ++i)
        if (ds.jobs[i].r.sec < 0) return 1;
    return 0;
}

/* Join only an exited worker in ordinary UI passes; its table replaces the cache here. */
static void duration_finish(void) {
    if (!ds.running || !__atomic_load_n(&ds.exited, __ATOMIC_ACQUIRE)) return;
    pthread_join(ds.thread, 0);
    /* ponytail: published once a pass ends; a long first pass shows its lengths only then */
    if (ds.out) free(ds.blob), ds.blob = ds.out, ds.n = ds.out_n, ds.out = 0;
    ds.whole = ds.done == (int)ds.total ? ds.pass : 0;
    duration_jobs_free();
    ds.running = ds.done = ds.exited = ds.cancel = 0;
}
/* The jobs handed to a worker; 0 when there are none or it can't start. */
static int duration_run(void) {
    ds.done = ds.exited = ds.cancel = 0;
    if (!ds.total || pthread_create(&ds.thread, 0, duration_worker, 0)) {
        duration_jobs_free();
        return 0;
    }
    ds.running = 1;
    return 1;
}

void durations_cancel(void) {
    if (!ds.running) duration_jobs_free(); /* an update's jobs, waiting for its dialog */
    __atomic_store_n(&ds.due, 0, __ATOMIC_RELEASE);
    __atomic_store_n(&ds.cancel, 1, __ATOMIC_RELEASE);
}
/* ponytail: a clean end (bounded) waits up to DUR_WAIT_MS for the current TagLib call, one still
 * running past it left to the kill, its pass unsaved; USB storage waits it out, as playlist art's
 * stop does, the armed watchdog its bound. */
void durations_shutdown(int bounded) {
    durations_cancel();
    for (int i = 0; (!bounded || i < DUR_WAIT_MS / 10) && ds.running &&
                    !__atomic_load_n(&ds.exited, __ATOMIC_ACQUIRE);
         ++i)
        usleep(10000);
    duration_finish();
}
int durations_busy(int *done, int *total) {
    duration_finish();
    if (done) *done = __atomic_load_n(&ds.done, __ATOMIC_ACQUIRE);
    if (total) *total = (int)ds.total;
    return ds.running;
}

/* Long Songs' pass over songs, its own copy of the library, whatever the switch: none when a whole
 * pass already ran over this library or every song has a length, so a restart walks nothing but
 * songs gone unmeasured. The caller's deque never crosses the thread boundary; a running pass is
 * the one asked for. */
int durations_start(void *songs) {
    duration_finish();
    if (ds.running) return 1;
    if (ds.whole == library_gen + 1 || library_scanning() ||
        navigator_window_is_exist("usbmode_page"))
        return 0;
    duration_jobs_free(); /* an update's waiting jobs: these records are the library too */
    duration_read();
    ds.pass = library_gen + 1; /* the generation goes with the read, not the worker's start */
    unsigned size = deque_size(songs);
    for (unsigned i = 0; i < size; ++i)
        if (!duration_job(P(deque_at(songs, i), REC_PATH))) {
            duration_jobs_free();
            return 0;
        }
    /* ponytail: edits in place wait for an update's pass; a song whose file went before any pass
     * measured it stays without a length until an update drops its row, so the first open after
     * each power-on checks every song */
    if (!duration_unknown()) {
        duration_jobs_free();
        return 0;
    }
    return duration_run();
}

/* Update Local Music's or a folder scan's end, on stock's scan thread: with the switch on, the UI's
 * next pass reads the library's paths and measures them. Not inline here: the update's dialog,
 * and the stop on playback it began with, would stay up for the whole pass. */
void durations_scanned(void) {
    if (st.scan_durations) __atomic_store_n(&ds.due, 1, __ATOMIC_RELEASE);
}

static int duration_row(void *ctx, int n, char **v, char **c) {
    return !duration_job(v[0]); /* nonzero: sqlite3_exec stops */
}
/* The library's paths, in one read inside the closing update dialog, so its new songs are known
 * as it goes; any failure drops them all. */
static void duration_library(void) {
    ds.pass = library_gen + 1; /* a later scan meanwhile is due again */
    duration_read();
    if (!era_sql("select fileurl from songtable where " MUSIC_WHERE, duration_row, 0))
        duration_jobs_free();
}

/* Every UI pass: a finished pass joined, Standby held off while one runs, the card's hand-over or a
 * scan stopping it, an update's paths read once it ends and measured once its dialog is gone. */
void durations_poll(void) {
    duration_finish();
    if (ds.running) {
        reset_poweroptions_timer(1, 1, 0); /* Standby unmounts the card */
        /* the stop alone: a scan's fresh due stays, and the jobs are the worker's */
        if (library_scanning() || navigator_window_is_exist("usbmode_page"))
            __atomic_store_n(&ds.cancel, 1, __ATOMIC_RELEASE);
        return;
    }
    if (ds.total) {
        /* AWTK's nested loops quit newest first: a toast under the closing dialog would take its
         * quit. USB mode's exit asks for the scan before its page goes. */
        if (navigator_window_is_exist("updatemusic_dialog") ||
            navigator_window_is_exist("usbmode_page"))
            return;
        int fresh = duration_unknown();
        /* ponytail: once, as the dialog goes, or never: no late pop-up. A song whose file went
         * before any pass measured it stays without a length until an update drops its row, so
         * each update shows the message; edits are measured without it. duration_run() comes
         * first: the toast's loop polls again, finding the pass running. */
        if (duration_run() && fresh && usable() && !g_poweroff_state && !st.toasts &&
            !pointer_down())
            toast("Measuring song lengths\xe2\x80\xa6");
        return;
    }
    if (!__atomic_load_n(&ds.due, __ATOMIC_ACQUIRE) || library_scanning()) return;
    __atomic_store_n(&ds.due, 0, __ATOMIC_RELEASE);
    if (ds.whole == library_gen + 1) return; /* Long Songs measured this library meanwhile */
    duration_library();
}
