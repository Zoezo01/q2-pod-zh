/* Shared song durations. Only the UI owns the cache; a worker reads an immutable snapshot and
 * writes its own jobs. Results are published only after pthread_join. No TagLib or stat in a
 * duration lookup. See docs/internals.md, "Song durations". */
#include "navigation.h"

#define DUR_FILE "/mnt/data/ringnav-durations-v1"
#define DUR_MAX (16u * 1024u * 1024u)
#define DUR_SONGS 100000u
typedef struct {
    char *path;
    unsigned size, mtime, ns;
    int sec; /* -1: unavailable, 0: tried but no duration */
} duration_t;
static struct {
    duration_t *tab, *jobs;
    unsigned n, total, read, writable, timer_at, gen, seen;
    unsigned long thread;
    int running, done, cancel, exited, mounted;
} ds __attribute__((section(".scratch")));

static int duration_order(const void *a, const void *b) {
    return strcmp(((const duration_t *)a)->path, ((const duration_t *)b)->path);
}
static void duration_free(duration_t *tab, unsigned n) {
    for (unsigned i = 0; i < n; ++i) free(tab[i].path);
    free(tab);
}
static duration_t *duration_find(const char *path) {
    unsigned lo = 0, hi = ds.n;
    if (!path) return 0;
    while (lo < hi) {
        unsigned mid = lo + (hi - lo) / 2;
        if (strcmp(ds.tab[mid].path, path) < 0)
            lo = mid + 1;
        else
            hi = mid;
    }
    return lo < ds.n && !strcmp(ds.tab[lo].path, path) ? ds.tab + lo : 0;
}

/* Disk: magic, then {path bytes incl NUL, size, mtime, ns, seconds}, path, repeated. Unaligned
 * words use memcpy. The old hash-only ringnav-lengths stays untouched: it cannot be validated. */
static void duration_read(void) {
    if (ds.read || ds.running || ds.tab) return;
    unsigned size = 0, at = 8, n = 0;
    char *data = read_all(DUR_FILE, DUR_MAX, &size);
    ds.writable = data || file_size(DUR_FILE) <= 0;
    ds.read = ds.writable;
    if (!data) return;
    if (size < 8 || memcmp(data, "Q2DUR1\0", 8)) goto out;
    /* Validate the entire file before accepting any entry. */
    while (at < size) {
        unsigned h[5];
        if (size - at < sizeof h || n == DUR_SONGS) goto out;
        memcpy(h, data + at, sizeof h);
        at += sizeof h;
        if (h[0] < 2 || h[0] > 4096 || h[0] > size - at || data[at] != '/' || data[at + h[0] - 1] ||
            strlen(data + at) != h[0] - 1 || h[4] > 0x7fffffffu)
            goto out;
        at += h[0];
        ++n;
    }
    duration_t *tab = calloc(n + 1, sizeof *tab);
    if (!tab) {
        ds.read = ds.writable = 0;
        goto out;
    }
    at = 8;
    for (unsigned i = 0; i < n; ++i) {
        unsigned h[5];
        memcpy(h, data + at, sizeof h);
        at += sizeof h;
        tab[i] = (duration_t){ strdup(data + at), h[1], h[2], h[3], (int)h[4] };
        if (!tab[i].path) {
            duration_free(tab, i);
            ds.read = ds.writable = 0;
            goto out;
        }
        at += h[0];
    }
    qsort(tab, n, sizeof *tab, duration_order);
    for (unsigned i = 1; i < n; ++i)
        if (!strcmp(tab[i - 1].path, tab[i].path)) {
            duration_free(tab, n);
            goto out;
        }
    ds.tab = tab;
    ds.n = n;
out:
    free(data);
}

int duration_seconds(void *song) {
    if (!song) return -1;
    int start = I(song, REC_CUE_START), end = I(song, REC_CUE_END);
    if (start < 0) start = 0;
    if (end > start) return end - start;
    duration_read();
    duration_t *entry = duration_find(P(song, REC_PATH));
    return !entry || entry->sec < 0 ? -1 : entry->sec > start ? entry->sec - start : 0;
}
/* No fabricated 0:00 for unknown or unreadable files. Hours are kept for long recordings. */
const char *duration_text(char *text, unsigned size, void *song) {
    int sec = duration_seconds(song);
    if (sec <= 0) {
        if (size) text[0] = 0;
        return text;
    }
    if (sec >= 3600)
        tk_snprintf(text, size, "%d:%02d:%02d", sec / 3600, sec / 60 % 60, sec % 60);
    else
        tk_snprintf(text, size, "%d:%02d", sec / 60, sec % 60);
    return text;
}

static int duration_stat(const char *path, unsigned *stamp) {
    unsigned info[36]; /* audited o32 stat: size 12, mtime 16, nanoseconds 17 */
    if (__xstat(3, path, info) || (info[5] & 0170000) != 0100000) return 0;
    stamp[0] = info[12], stamp[1] = info[16], stamp[2] = info[17];
    return 1;
}
static void *duration_worker(void *unused) {
    for (unsigned i = 0; i < ds.total && !__atomic_load_n(&ds.cancel, __ATOMIC_ACQUIRE); ++i) {
        duration_t *job = ds.jobs + i, *old = duration_find(job->path);
        unsigned before[3], after[3];
        if (duration_stat(job->path, before)) {
            if (old && !memcmp(before, &old->size, sizeof before))
                job->sec = old->sec;
            else {
                /* As Long Songs did: don't hand TagLib an oversized Ogg header. */
                void *f = ogg_tags(job->path, 0) ? 0 : taglib_file_new(job->path, 1);
                void *p = f ? taglib_file_audioproperties(f) : (void *)0;
                int sec = p ? ((int (*)(void *))(*(void ***)p)[3])(p) : 0;
                if (f) taglib_file_free(f);
                job->sec = sec > 0 ? sec : 0;
            }
            if (duration_stat(job->path, after) && !memcmp(before, after, sizeof before))
                memcpy(&job->size, before, sizeof before);
            else
                job->sec = -1; /* changed during probe: try again at the next scan */
        }
        __atomic_store_n(&ds.done, (int)i + 1, __ATOMIC_RELEASE);
    }
    __atomic_store_n(&ds.exited, 1, __ATOMIC_RELEASE);
    return 0;
}

static void duration_save(void) {
    unsigned size = 8;
    if (!ds.writable || ds.n > DUR_SONGS) return;
    for (unsigned i = 0; i < ds.n; ++i) {
        unsigned len = strlen(ds.tab[i].path) + 1;
        if (len + 20 > DUR_MAX - size) return;
        size += 20 + len;
    }
    char *data = calloc(size, 1);
    if (!data) return;
    memcpy(data, "Q2DUR1\0", 8);
    unsigned at = 8;
    for (unsigned i = 0; i < ds.n; ++i) {
        duration_t *t = ds.tab + i;
        unsigned h[] = { strlen(t->path) + 1, t->size, t->mtime, t->ns, (unsigned)t->sec };
        memcpy(data + at, h, sizeof h);
        at += sizeof h;
        memcpy(data + at, t->path, h[0]);
        at += h[0];
    }
    if (!blob_io(DUR_FILE, DUR_FILE ".tmp", data, size, 1)) unlink(DUR_FILE ".tmp");
    free(data);
}

/* Join only an exited worker in ordinary UI passes. All ownership transfers happen here. */
static void duration_finish(void) {
    if (!ds.running || !__atomic_load_n(&ds.exited, __ATOMIC_ACQUIRE)) return;
    pthread_join(ds.thread, 0);
    unsigned done = (unsigned)ds.done, n = 0, a = 0, b = 0;
    int changed = 0;
    duration_t *tab = calloc(ds.n + done + 1, sizeof *tab);
    if (tab) {
        while (a < ds.n || b < done) {
            int cmp = a == ds.n ? 1 : b == done ? -1 : duration_order(ds.tab + a, ds.jobs + b);
            if (cmp < 0) {
                if (done == ds.total)
                    free(ds.tab[a++].path), changed = 1; /* deleted song */
                else
                    tab[n++] = ds.tab[a++];
            } else {
                if (cmp || ds.jobs[b].sec < 0 ||
                    memcmp(&ds.tab[a].size, &ds.jobs[b].size, 4 * sizeof(unsigned)))
                    changed = 1;
                if (!cmp) free(ds.tab[a++].path);
                if (ds.jobs[b].sec >= 0) tab[n++] = ds.jobs[b], ds.jobs[b].path = 0;
                ++b;
            }
        }
        free(ds.tab);
        ds.tab = tab, ds.n = n;
        if (changed) duration_save();
    } else
        ds.seen = 0; /* retry after an allocation failure */
    duration_free(ds.jobs, ds.total);
    ds.jobs = 0;
    ds.running = ds.done = ds.total = ds.exited = ds.cancel = 0;
}

void durations_cancel(void) {
    __atomic_store_n(&ds.cancel, 1, __ATOMIC_RELEASE);
    ds.seen = 1;
    ds.gen = library_gen; /* Cancel stays cancelled until another update, remount or toggle. */
}
void durations_shutdown(void) {
    durations_cancel();
    while (ds.running && !__atomic_load_n(&ds.exited, __ATOMIC_ACQUIRE)) usleep(10000);
    duration_finish();
}
int durations_busy(int *done, int *total) {
    duration_finish();
    if (done) *done = __atomic_load_n(&ds.done, __ATOMIC_ACQUIRE);
    if (total) *total = (int)ds.total;
    return ds.running;
}

/* The caller's deque never crosses the thread boundary. Duplicate CUE paths are probed once. */
int durations_start(void *songs) {
    duration_finish();
    if (ds.running || !st.scan_durations) return ds.running;
    if (library_scanning() || navigator_window_is_exist("usbmode_page")) return 0;
    duration_read();
    unsigned size = deque_size(songs), n = 0;
    if (size > DUR_SONGS) return 0;
    duration_t *jobs = calloc(size + 1, sizeof *jobs);
    if (!jobs) return 0;
    for (unsigned i = 0; i < size; ++i) {
        void *r = deque_at(songs, i);
        const char *path = P(r, REC_PATH);
        if (!path || path[0] != '/' || strlen(path) >= 4096) continue;
        jobs[n].path = strdup(path);
        if (!jobs[n].path) {
            duration_free(jobs, n);
            return 0;
        }
        jobs[n++].sec = -1;
    }
    qsort(jobs, n, sizeof *jobs, duration_order);
    unsigned k = 0;
    for (unsigned i = 0; i < n; ++i) {
        if (k && !strcmp(jobs[k - 1].path, jobs[i].path))
            free(jobs[i].path);
        else
            jobs[k++] = jobs[i];
    }
    if (!k) {
        free(jobs);
        return 0;
    }
    ds.jobs = jobs, ds.total = k;
    ds.done = ds.exited = ds.cancel = 0;
    if (pthread_create(&ds.thread, 0, duration_worker, 0)) {
        duration_free(jobs, k);
        ds.jobs = 0;
        ds.total = 0;
        return 0;
    }
    ds.running = 1;
    ds.gen = library_gen;
    ds.seen = ds.mounted = 1;
    return 1;
}

/* Boot, remount and library changes trigger one incremental pass. Never query a changing DB or
 * read the card while USB storage owns it. A disabled switch cancels without blocking the UI. */
void durations_poll(void) {
    duration_finish();
    if (!st.scan_durations) {
        if (ds.running) durations_cancel();
        ds.seen = 0;
        return;
    }
    unsigned now = time_now_ms();
    if (ds.timer_at && now - ds.timer_at < 1000) return;
    ds.timer_at = now | !now;
    int mounted = !navigator_window_is_exist("usbmode_page") && card_mounted();
    if (!mounted || library_scanning()) {
        if (ds.running) durations_cancel();
        ds.seen = 0;
        ds.mounted = mounted;
        return;
    }
    if (!ds.mounted) ds.seen = 0;
    ds.mounted = 1;
    unsigned gen = library_gen;
    if ((gen & 1) || ds.running || (ds.seen && ds.gen == gen)) return;
    int n = 0;
    void *songs = staged(songs_unsorted, (void *)1, &n);
    if (songs && gen == library_gen && !library_scanning()) {
        int ok = durations_start(songs);
        if (ok || !deque_size(songs)) ds.gen = gen, ds.seen = 1;
    }
    if (songs) deque_destroy(songs);
}
