/* Portable host regression harness for the actual shared service, without stock firmware. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define Q2_NAVIGATION_H
typedef struct {
    const char *path;
    int cue, end;
} Song;
typedef struct {
    unsigned n;
    Song **songs;
} Deque;
#define REC_PATH 0
#define REC_CUE_START ((int)sizeof(void *))
#define REC_CUE_END ((int)sizeof(void *) + 4)
#define I(p, o) (*(int *)((char *)(p) + (o)))
#define P(p, o) (*(void **)((char *)(p) + (o)))
#define tk_snprintf snprintf
static struct {
    int scan_durations;
} st;
static volatile unsigned library_gen;
static unsigned now;
static int mounted = 1, usb, scanning, probes, stats, writes, joins, create_fail, read_fail,
           write_fail;
static void *(*pending)(void *);
static Deque *library;
static unsigned char *disk;
static unsigned disk_n;
typedef struct {
    const char *path;
    int sec, present;
    unsigned size, mtime, ns;
    int changes;
} File;
static File files[8];
static File *file_at(const char *path) {
    for (unsigned i = 0; i < 8; ++i)
        if (files[i].path && !strcmp(files[i].path, path)) return files + i;
    return 0;
}
static void *read_all(const char *path, unsigned max, unsigned *size) {
    (void)path;
    *size = 0;
    if (!disk || read_fail || disk_n > max) return 0;
    void *p = malloc(disk_n ? disk_n : 1);
    assert(p);
    memcpy(p, disk, disk_n);
    *size = disk_n;
    return p;
}
static long file_size(const char *path) {
    (void)path;
    return disk ? (long)disk_n : -1;
}
static int blob_io(const char *path, const char *tmp, void *p, unsigned n, int save) {
    (void)path;
    (void)tmp;
    assert(save);
    if (write_fail) return 0;
    free(disk);
    disk = malloc(n);
    assert(disk);
    memcpy(disk, p, n);
    disk_n = n;
    ++writes;
    return 1;
}
static int fake_unlink(const char *path) {
    (void)path;
    return 0;
}
#define unlink fake_unlink
static int fake_stat(int ver, const char *path, void *out) {
    assert(ver == 3);
    ++stats;
    File *f = file_at(path);
    if (!f || !f->present) return -1;
    unsigned *s = out;
    memset(s, 0, 36 * sizeof *s);
    s[5] = 0100644;
    s[12] = f->size;
    s[16] = f->mtime;
    s[17] = f->ns;
    return 0;
}
#define __xstat fake_stat
static int ogg_tags(const char *path, void *unused) {
    (void)path;
    (void)unused;
    return 0;
}
typedef struct {
    void **vtable;
    File *file;
} Props;
static int audio_length(void *p) { return ((Props *)p)->file->sec; }
static void *taglib_file_new(const char *path, int mode) {
    static void *vtable[4];
    static Props props;
    ++probes;
    assert(mode == 1);
    File *f = file_at(path);
    if (!f || f->sec < 0) return 0;
    if (f->changes) ++f->mtime;
    vtable[3] = (void *)audio_length;
    props.vtable = vtable;
    props.file = f;
    return &props;
}
static void *taglib_file_audioproperties(void *f) { return f; }
static void taglib_file_free(void *f) { (void)f; }
static int fake_create(unsigned long *tid, const void *attr, void *(*fn)(void *), void *arg) {
    (void)attr;
    (void)arg;
    if (create_fail) return 1;
    assert(!pending);
    *tid = 1;
    pending = fn;
    return 0;
}
static int fake_join(unsigned long tid, void **out) {
    (void)out;
    assert(tid == 1 && !pending);
    ++joins;
    return 0;
}
#define pthread_create fake_create
#define pthread_join fake_join
static void run_worker(void) {
    assert(pending);
    void *(*fn)(void *) = pending;
    pending = 0;
    fn(0);
}
static int usleep(unsigned us) {
    (void)us;
    if (pending) run_worker();
    return 0;
}
static unsigned deque_size(const void *q) { return q ? ((const Deque *)q)->n : 0; }
static void *deque_at(const void *q, unsigned i) {
    assert(i < ((const Deque *)q)->n);
    return ((const Deque *)q)->songs[i];
}
static void deque_destroy(void *q) { free(q); }
static int songs_unsorted(void *q) {
    (void)q;
    return 0;
}
static void *staged(int (*query)(void *), void *arg, int *n) {
    (void)query;
    (void)arg;
    Deque *q = malloc(sizeof *q);
    assert(q);
    *q = *library;
    *n = (int)q->n;
    return q;
}
static unsigned time_now_ms(void) { return now; }
static int navigator_window_is_exist(const char *name) {
    assert(!strcmp(name, "usbmode_page"));
    return usb;
}
static int card_mounted(void) { return mounted; }
static int library_scanning(void) { return scanning; }
#include "../patch/durations.c"

static void reset(int keep_disk) {
    assert(!pending && !ds.running);
    duration_free(ds.tab, ds.n);
    memset(&ds, 0, sizeof ds);
    if (!keep_disk) {
        free(disk);
        disk = 0;
        disk_n = 0;
    }
    probes = stats = writes = joins = create_fail = read_fail = write_fail = 0;
    memset(files, 0, sizeof files);
    st.scan_durations = 1;
    now = 1000;
    mounted = 1;
    usb = scanning = 0;
    library_gen = 0;
}
static void file(unsigned i, const char *path, int sec) {
    files[i] = (File){ path, sec, 1, 1000, 2000, 0, 0 };
}
static void scan(Deque *q) {
    assert(durations_start(q));
    run_worker();
    assert(!durations_busy(0, 0));
}
int main(void) {
    Song a = { "/mnt/mmc/a.flac", 0, 0 }, b = { "/mnt/mmc/b.flac", 0, 0 },
         cue = { "/mnt/mmc/a.flac", 100, 300 };
    Song *rows[] = { &a, &b, &cue };
    Deque q = { 3, rows };
    library = &q;
    reset(0);
    file(0, a.path, 225);
    file(1, b.path, 3601);
    assert(duration_seconds(&a) == -1 && duration_seconds(0) == -1 &&
           duration_seconds(&cue) == 200);
    st.scan_durations = 0;
    assert(!durations_start(&q) && !pending);
    st.scan_durations = 1;
    assert(durations_start(&q));
    assert(ds.total == 2);
    assert(durations_start(&q) && ds.total == 2); /* one worker */
    run_worker();
    assert(duration_seconds(&a) == -1); /* not published yet */
    assert(!durations_busy(0, 0) && joins == 1 && probes == 2 && writes == 1);
    assert(duration_seconds(&a) == 225 && duration_seconds(&b) == 3601);
    char text[24];
    assert(!strcmp(duration_text(text, sizeof text, &a), "3:45"));
    assert(!strcmp(duration_text(text, sizeof text, &b), "1:00:01"));
    cue.end = 0;
    assert(duration_seconds(&cue) == 125);
    cue.cue = 500;
    assert(duration_seconds(&cue) == 0);
    int oldstats = stats, oldprobes = probes;
    duration_seconds(&a);
    assert(stats == oldstats && probes == oldprobes);
    scan(&q);
    assert(probes == 2 && writes == 1); /* stat only, no flash rewrite */
    ++files[0].size;
    files[0].sec = 300;
    scan(&q);
    assert(probes == 3 && duration_seconds(&a) == 300);
    ++files[0].mtime;
    files[0].sec = 301;
    scan(&q);
    assert(probes == 4 && duration_seconds(&a) == 301);
    ++files[0].ns;
    files[0].sec = 302;
    scan(&q);
    assert(probes == 5 && duration_seconds(&a) == 302);
    reset(1);
    assert(duration_seconds(&a) == 302); /* persistent cache reload */
    assert(probes == 0 && stats == 0);
    file(0, a.path, 302);
    file(1, b.path, 3601);
    assert(durations_start(&q));
    durations_cancel();
    assert(durations_busy(0, 0) && !joins);
    run_worker();
    assert(!durations_busy(0, 0) && duration_seconds(&a) == 302 && writes == 0);
    files[0].present = 0;
    scan(&q);
    assert(duration_seconds(&a) == -1); /* missing path never becomes 0 */
    files[0].present = 1;
    files[0].sec = -1;
    scan(&q);
    assert(duration_seconds(&a) == 0);
    oldprobes = probes;
    scan(&q);
    assert(probes == oldprobes); /* unreadable TagLib result cached */
    ++files[0].mtime;
    files[0].sec = 500;
    files[0].changes = 1;
    scan(&q);
    assert(duration_seconds(&a) == -1); /* file changed during parse */
    files[0].changes = 0;
    scan(&q);
    assert(duration_seconds(&a) == 500);
    Deque only_a = { 1, rows };
    scan(&only_a);
    assert(duration_seconds(&b) == -1); /* prune deleted */
    reset(1);
    read_fail = 1;
    file(0, a.path, 600);
    scan(&only_a);
    assert(duration_seconds(&a) == 600 && writes == 0);
    reset(1);
    assert(duration_seconds(&a) == 500); /* unreadable file was never overwritten */
    reset(0);
    file(0, a.path, 700);
    write_fail = 1;
    scan(&only_a);
    assert(duration_seconds(&a) == 700 && !disk);
    reset(0);
    create_fail = 1;
    assert(!durations_start(&q) && !ds.jobs && !ds.running);
    reset(0);
    file(0, a.path, 225);
    file(1, b.path, 3601);
    durations_poll();
    assert(pending);
    run_worker();
    now += 1000;
    durations_poll();
    assert(!pending);
    now += 1000;
    durations_poll();
    assert(!pending); /* one automatic pass per generation */
    library_gen += 2;
    now += 1000;
    durations_poll();
    assert(pending);
    durations_cancel();
    run_worker();
    now += 1000;
    durations_poll();
    assert(!pending); /* Cancel doesn't restart automatically */
    st.scan_durations = 0;
    durations_poll();
    st.scan_durations = 1;
    now += 1000;
    durations_poll();
    assert(pending);
    st.scan_durations = 0;
    durations_poll();
    run_worker();
    durations_poll();
    assert(!ds.running);
    st.scan_durations = 1;
    usb = 1;
    now += 1000;
    durations_poll();
    assert(!pending);
    usb = 0;
    scanning = 1;
    now += 1000;
    durations_poll();
    assert(!pending);
    scanning = 0;
    mounted = 0;
    now += 1000;
    durations_poll();
    assert(!pending);
    mounted = 1;
    now += 1000;
    durations_poll();
    assert(pending);
    durations_shutdown();
    assert(!ds.running && !pending);
    /* Corruption: the parser accepts no prefix of a malformed record. */
    reset(0);
    disk = malloc(32);
    disk_n = 32;
    memset(disk, 'x', 32);
    assert(duration_seconds(&a) == -1 && !ds.n);
    memcpy(disk, "Q2DUR1\0", 8);
    ds.read = 0;
    assert(duration_seconds(&a) == -1 && !ds.n);
    reset(0);
    puts("duration service regressions passed");
    return 0;
}
