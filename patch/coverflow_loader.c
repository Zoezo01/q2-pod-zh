/* Cover Flow's loader and half copies: the half copies fx_window wants, read on a thread of their
 * own, and made at rest by the idle passes. */
#include "coverflow.h"

/* Half copies (docs/internals.md#coverflow-depth): HALF x HALF texels, each the mean of four, raw
 * as a texture holds them beside the album's cache file (<key>.jpg.half), 25.6 KB read with one
 * fread and no decode. Going by letters the covers on the screen are all turned and narrow, never
 * face on, so half the art draws them as well, and a slide through tens of albums can load them as
 * it passes them. */
#define HALF (ART_SIZE / 2)
#define HALF_TEXELS (HALF * HALF)

/* Album key's half copy read whole into half, HALF_TEXELS; 0 when it has none whole. Files only:
 * the loader's thread calls it. */
static int half_load(unsigned key, unsigned *half) {
    char path[512];
    void *f = fopen(art_path(path, key, ".half"), "rb");
    int ok = f && fread(half, HALF_TEXELS * 4, 1, f) == 1;
    if (f) fclose(f);
    return ok;
}

/* The half copy in the last quarter of tex spread over the whole of it, each texel two by two, top
 * down: a half row is read before any write reaches it. */
static void half_spread(unsigned *tex) {
    const unsigned *half = tex + CF_TEXELS - HALF_TEXELS;
    for (int y = 0; y < HALF; ++y) {
        unsigned line[HALF], *o = tex + 2 * y * ART_SIZE;
        memcpy(line, half + y * HALF, sizeof line);
        for (int x = 0; x < HALF; ++x)
            o[2 * x] = o[2 * x + 1] = o[ART_SIZE + 2 * x] = o[ART_SIZE + 2 * x + 1] = line[x];
    }
}

int half_read(unsigned key, unsigned *tex) {
    int ok = half_load(key, tex + CF_TEXELS - HALF_TEXELS);
    if (ok) half_spread(tex);
    return ok;
}

/* Album key's half copy made from tex, its decoded cover, in place (each texel is read before its
 * own is written), written whole to .half.tmp and renamed, so a write cut short leaves none to
 * read. */
static void half_make(unsigned key, unsigned *tex) {
    char path[512], tmp[512];
    unsigned *half = tex, m = 0xff00ffu;
    art_path(path, key, ".half");
    for (int y = 0; y < HALF; ++y)
        for (int x = 0; x < HALF; ++x) { /* two channels a sum, each rounded */
            const unsigned *p = tex + 2 * y * ART_SIZE + 2 * x;
            unsigned a = p[0], b = p[1], c = p[ART_SIZE], d = p[ART_SIZE + 1];
            unsigned rb = (a & m) + (b & m) + (c & m) + (d & m) + 0x20002u,
                     ga = (a >> 8 & m) + (b >> 8 & m) + (c >> 8 & m) + (d >> 8 & m) + 0x20002u;
            half[y * HALF + x] = (rb >> 2 & m) | (ga >> 2 & m) << 8;
        }
    void *f = fopen(art_path(tmp, key, ".half.tmp"), "wb");
    int ok = f && fwrite(half, HALF_TEXELS * 4, 1, f) == 1;
    if (f && fclose(f)) ok = 0;
    if (!ok || rename(tmp, path)) unlink(tmp);
}

/* The loader (docs/internals.md#coverflow-depth): the half copies fx_window wants read on a thread
 * of its own, at the lowest priority, so the UI thread never waits on the card; it only copies one
 * that is there into its texture. A request goes LD_FREE to LD_WANT (the UI thread), LD_READ and
 * LD_READY (the loader), and back to LD_FREE (the UI thread, which also takes back one still
 * wanted that a frame no longer wants). Full art stays on the UI thread: decode goes through
 * AWTK's image manager, which is not thread safe. */
/* The o32 syscall nr(a, b, c): its result, or -1 when the kernel reports an error ($a3). demo
 * imports none of gettid, setpriority or ioprio_set. */
#ifndef PEQ_HOST
__asm__(".globl cf_syscall\ncf_syscall:\n.set push\n.set noreorder\nmove $v0, $a0\nmove $a0, $a1\n"
        "move $a1, $a2\nmove $a2, $a3\nsyscall\nbnez $a3, 1f\nnop\njr $ra\nnop\n1:\njr $ra\n"
        "li $v0, -1\n.set pop\n");
extern int cf_syscall(int nr, int a, int b, int c);
#else
__attribute__((weak)) int cf_syscall(int nr, int a, int b, int c) { return -1; } /* the tests' */
#endif
#define SYS_SETPRIORITY 4097 /* o32; 4096 is getpriority */
#define SYS_GETTID 4222
#define SYS_IOPRIO_SET 4314
#define IOPRIO(class, level) ((class) << 13 | (level)) /* idle 3, best effort 2 */

/* The calling thread below the UI and the player: nice 19, its reads in the idle I/O class if idle
 * and the kernel takes it, else in best effort's lowest level; the processes it forks (ffmpeg)
 * inherit both. The loader asks for idle; playlist_art.c's worker doesn't, as it takes stock's art
 * locks, which the UI thread waits on, and an idle-class holder gets the disk only when no one else
 * wants it. Returns what took: 1 for nice 19, plus the class << 1. Not static, so the compiler
 * keeps the return the loader ignores: test/patch.py runs it. */
__attribute__((noinline)) int thread_lower(int idle) {
    int tid = cf_syscall(SYS_GETTID, 0, 0, 0), io = 0;
    int nice = !cf_syscall(SYS_SETPRIORITY, 0 /* PRIO_PROCESS */, tid, 19);
    if (idle && !cf_syscall(SYS_IOPRIO_SET, 1 /* IOPRIO_WHO_PROCESS */, tid, IOPRIO(3, 0)))
        io = 3;
    else if (!cf_syscall(SYS_IOPRIO_SET, 1, tid, IOPRIO(2, 7)))
        io = 2;
    return nice | io << 1;
}
int ld_state(int s) { return __atomic_load_n(&fx.ld[s].state, __ATOMIC_ACQUIRE); }

/* The wanted request asked for first read into its stage; 0 when none is wanted. noinline:
 * test/patch.py runs it as the thread would. */
__attribute__((noinline)) static int ld_step(void) {
    int s, want;
    do {
        s = -1;
        for (int j = 0; j < LD_SLOTS; ++j)
            if (ld_state(j) == LD_WANT &&
                (s < 0 || __atomic_load_n(&fx.ld[j].rank, __ATOMIC_RELAXED) <
                              __atomic_load_n(&fx.ld[s].rank, __ATOMIC_RELAXED)))
                s = j;
        if (s < 0) return 0;
        want = LD_WANT; /* the UI thread may have taken it back meanwhile */
    } while (!__atomic_compare_exchange_n(&fx.ld[s].state, &want, LD_READ, 0, __ATOMIC_ACQUIRE,
                                          __ATOMIC_RELAXED));
    fx.ld[s].ok = half_load(fx.ld[s].key, fx.stage + s * HALF_TEXELS);
    __atomic_store_n(&fx.ld[s].state, LD_READY, __ATOMIC_RELEASE);
    return 1;
}

static void *loader(void *unused) {
    thread_lower(1);
    while (!__atomic_load_n(&fx.ld_stop, __ATOMIC_ACQUIRE))
        if (!ld_step()) sem_wait((void *)fx.gate);
    return 0;
}

/* album + 1's request, or -1 */
int ld_find(int album) {
    for (int s = 0; s < LD_SLOTS; ++s)
        if (ld_state(s) != LD_FREE && fx.ld[s].album == album) return s;
    return -1;
}

/* Request s given up if still only wanted, or read and not taken: 1 when it was. */
int ld_drop(int s) {
    int w = LD_WANT;
    if (__atomic_compare_exchange_n(&fx.ld[s].state, &w, LD_FREE, 0, __ATOMIC_RELAXED,
                                    __ATOMIC_RELAXED))
        return 1;
    if (w != LD_READY) return 0; /* free, or being read */
    __atomic_store_n(&fx.ld[s].state, LD_FREE, __ATOMIC_RELAXED);
    return 1;
}

/* The loader stopped after the read in hand and joined, its stage freed and every request free; it
 * starts again going by letters (ld_ask). */
void ld_quit(void) {
    if (fx.ld_on > 0) {
        __atomic_store_n(&fx.ld_stop, 1, __ATOMIC_RELEASE);
        sem_post((void *)fx.gate);
        pthread_join(fx.loader, 0);
    }
    free(fx.stage);
    fx.stage = 0, fx.ld_on = fx.ld_stop = 0;
    memset(fx.gate, 0, sizeof fx.gate);
    memset(fx.ld, 0, sizeof fx.ld); /* none left LD_READY over a stage freed */
}

/* The half copies of want's k albums the pool lacks asked for, in want's order, the loader started
 * at the first; a request no longer wanted is taken back, or its copy dropped. With LD_SLOTS in
 * hand, a request this frame has not reached yet (seen) gives up its place, a copy read for it and
 * not yet taken too, so the covers wanted first are always asked for. */
void ld_ask(const int *want, int k) {
    unsigned seen = 0;
    int asked = 0;
    if (!fx.ld_on && k) {
        fx.stage = calloc(LD_SLOTS, HALF_TEXELS * 4);
        fx.ld_on = fx.stage && !pthread_create(&fx.loader, 0, loader, 0) ? 1 : -1;
    }
    if (fx.ld_on < 0) return;
    for (int m = 0; m < k; ++m) {
        int i = fx_slot(want[m]), s = fx.art[i] ? LD_SLOTS : ld_find(want[m]);
        for (int j = 0; s < 0 && j < LD_SLOTS; ++j) s = ld_state(j) == LD_FREE ? j : -1;
        for (int j = 0; s < 0 && j < LD_SLOTS; ++j) s = !(seen >> j & 1) && ld_drop(j) ? j : -1;
        if (s < 0 || s == LD_SLOTS) continue; /* had, or none to spare: a later frame's */
        if (ld_state(s) == LD_FREE) {
            fx.ld[s].album = want[m];
            fx.ld[s].key = album_key(deque_at(cf.albums, (unsigned)want[m] - 1));
            __atomic_store_n(&fx.ld[s].rank, m, __ATOMIC_RELAXED);
            __atomic_store_n(&fx.ld[s].state, LD_WANT, __ATOMIC_RELEASE);
            asked = 1;
        } else
            __atomic_store_n(&fx.ld[s].rank, m, __ATOMIC_RELAXED);
        seen |= 1u << s;
    }
    for (int s = 0; s < LD_SLOTS; ++s)
        if (!(seen >> s & 1)) ld_drop(s);
    if (asked) sem_post((void *)fx.gate);
}

/* Request s's copy into pool slot i (album a): asked for again unless it is still this album's,
 * read since the last Refresh Coverflow; then spread there, or its own art decoded when it has
 * none whole. */
void ld_take(int s, int i, int a) {
    unsigned *buf = fx_buf(i);
    unsigned key = album_key(deque_at(cf.albums, (unsigned)a));
    if (fx.ld[s].key != key) {
        fx.todo = 1; /* asked for again */
    } else if (!fx.ld[s].ok)
        fx_fill(i, a, 0);
    else {
        memcpy(buf + CF_TEXELS - HALF_TEXELS, fx.stage + s * HALF_TEXELS, HALF_TEXELS * 4);
        half_spread(buf);
        fx.art[i] = buf, fx.half[i] = 1;
    }
    __atomic_store_n(&fx.ld[s].state, LD_FREE, __ATOMIC_RELAXED);
}

/* At rest under a thumb theme (coverflow_wheel_poll's idle passes): the half copies the cache still
 * lacks, made one a pass from the cover, no sooner than HALF_GAP_MS after the last (a decode is 6
 * to 9 ms of the UI thread), HALF_LOOK albums looked at a pass at most, none with the card's free
 * space under the larger of HALF_KEEP_MB and HALF_KEEP_PCT of its size (they stop short of filling
 * it and stay until Refresh Cover Flow), and none outside Fast Navigation or for playlists; from
 * the first album each time the page opens, done at the last. Each is decoded into a texture's
 * worth of heap of its own, freed once written. The only place they are written: never in a frame.
 */
#define HALF_LOOK 32
#define HALF_GAP_MS 100
#define HALF_KEEP_MB 512
#define HALF_KEEP_PCT 5
void coverflow_halves(void) {
    int n = cf.albums && fx.tex ? (int)deque_size(cf.albums) : 0;
    unsigned now = (unsigned)time_now_ms();
    char url[600] = "file://", path[512];
    if (fx.halves >= n || !ringnav_big_library() || cf.scoped == PLAYLISTS ||
        (fx.half_at && (int)(now - fx.half_at) < HALF_GAP_MS) ||
        !card_room(ART_DIR, HALF_KEEP_MB, HALF_KEEP_PCT))
        return;
    for (int look = 0; fx.halves < n && look < HALF_LOOK; ++look) {
        unsigned key = album_key(deque_at(cf.albums, (unsigned)fx.halves++));
        if (!access(art_path(path, key, ".half"), 0) || file_size(art_path(url + 7, key, "")) <= 0)
            continue; /* made, or no art */
        fx.half_at = now ? now : 1;
        unsigned *tex = calloc(CF_TEXELS, 4);
        if (tex && cf_decode(url, tex, 1)) half_make(key, tex);
        free(tex);
        return;
    }
}
#ifdef PEQ_HOST
/* The loader: its thread's body, one step of it, and album a's request (LD_*, -1 none), or with a
 * -1 how many are in hand. */
void *(*cf_loader(void))(void *) { return loader; }
int cf_ld_step(void) { return ld_step(); }
int cf_ld(int a) {
    int s = a < 0 ? 0 : ld_find(a + 1);
    for (int j = 0; a < 0 && j < LD_SLOTS; ++j) s += ld_state(j) != LD_FREE;
    return a < 0 || s < 0 ? s : ld_state(s);
}
#endif
