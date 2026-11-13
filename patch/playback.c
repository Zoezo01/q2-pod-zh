/* Grouped queue traversal. Indices identify occurrences, including duplicate/CUE entries.
 * Stock still owns the queue, decoder and gapless handoff. */
#include "playback.h"
#include "chapters.h"
#include "offsets.inc"
#include "peq.h"
#include "stock.h"
#define M(a) (*(volatile int *)(a))
#define QUEUE_FILE "/mnt/data/ringnav-queue"
#define CHECKPOINT_FILE QUEUE_FILE "-elapsed"
#define QUEUE_LIMIT 65536
#define HISTORY_LIMIT 4096
/* ponytail: Play Next's pending list holds 64 queue indices; a new one past that drops the oldest
 * (the last). Raise it, a fixed array in the struct, if more are wanted. */
#define PEND_LIMIT 64
/* ponytail: a larger queue is not kept across a reboot, as the snapshot is the whole queue,
 * rewritten each track change; save the order once and positions after to lift it. */
#define SAVE_LIMIT 999
extern int stock_load_trampoline(void *, int, int), stock_next_trampoline(int),
    stock_prev_trampoline(void), stock_mode_trampoline(int), stock_preload_trampoline(void),
    stock_memory_trampoline(void *), stock_savequeue_trampoline(void), stock_change_trampoline(int),
    stock_seek_time_trampoline(int), stock_seek_start_trampoline(void),
    stock_reboot_trampoline(void), stock_checkfw_trampoline(void *, void *),
    stock_speed_trampoline(int), stock_open_preload_trampoline(void), stock_start_queue_trampoline(int),
    stock_player_start_trampoline(void *, int, int, int);
extern void *staged(int (*)(void *), void *, int *);
extern int album_before(const void *, const void *), folder_cmp(void *, void *),
    album_only(void *, void *, int);
extern unsigned queue_hash(void *); /* navigation.c */
extern int resume_held(void);       /* navigation.c */
extern void resume_return(void), resume_restart(void);

typedef struct {
    unsigned index, group, rank;
} entry;
typedef struct {
    int active, shuffle, repeat, folder, restoring, dirty, grouping;
    unsigned n, cursor, npend, hn, stamp;
    unsigned pend[PEND_LIMIT]; /* Play Next: queue indices, the first plays first */
    int saved_elapsed, save_failed, resume_pending, resume_elapsed, resume_wait;
    unsigned saved_pos, saved_sum, saved_queue; /* saved_queue: a plain snapshot's queue_hash */
    int next_valid, next_value;
    unsigned next_pos;
    unsigned resume_key, resume_pos;
    entry *order, *cycle;
    unsigned *groups, *seen, *history;
    unsigned poll_stamp;
} playback;
static playback s __attribute__((section(".scratch")));
/* Each queue entry's identity (its path's hash and CUE start) as the last rebuild took the queue, so
 * follow_queue can tell which entries a stock edit took out; and playback_poll's thread, the UI's. */
static unsigned *ids __attribute__((section(".scratch")));
static unsigned long ui_thread __attribute__((section(".scratch")));
/* Stock Shuffle's Previous plays the last track, but the track it left stays unplayed in the pool,
 * so stock's Next draws a random one instead: ringnav_shuffle returns to it (playback_back).
 * back_to is that index + 1, back_at where Previous landed. */
static int back_to __attribute__((section(".scratch"))), back_at __attribute__((section(".scratch")));
/* Shuffle Songs is one-shot: the stock mode it replaced + 1, which the next other queue restores.
 * ponytail: RAM only, so a reboot mid-shuffle keeps Shuffle; a queue-file header field fixes it. */
static int unshuffle __attribute__((section(".scratch")));
unsigned playback_loads __attribute__((section(".scratch")));
/* The wheel's ticks. Stock's encoder thread looks at the wheel once per 80 ms (200 ms after
 * touching) and ticks at most once a look, dropping any travel past its threshold: on the device
 * a tick took 27 units of travel against a threshold of 12, a fast half turn gave a single tick,
 * and a touch's first 200 ms none. Here every WHEEL_TICK_UNITS of travel is a tick and the rest
 * carries; a reversal starts the count again, so turning back is as precise as starting. The
 * encoder's ts_read call (build.py WHEEL_TS_READ) runs through ringnav_ts_read, which posts the
 * ticks as stock does and then hands stock a still finger (the touch-down x), so it posts none. */
static int wh_last __attribute__((section(".scratch")));
static int wh_acc __attribute__((section(".scratch")));
static int wh_anchor __attribute__((section(".scratch"))); /* touch-down x (1-200); 0 lifted */
static int wh_units __attribute__((section(".scratch")));  /* wh_acc's tick, both in 1/16 unit */

/* Wheel sensitivity (System settings -> Display, crossfade.c's page): 10-200% in tens, Q2POD
 * WHEELSENSITIVITY in config.ini, anything else 50%. The travel per tick is 50% / sensitivity of
 * WHEEL_TICK_UNITS: 50% is today's 28 (a seventh of a turn), 100% 14, 200% 7, 1.0.6's scale and
 * key, so a value saved then means what it did (within the half unit 1.0.6 rounded to). It is kept
 * in sixteenths of a unit (WHEEL_FINE), so every step differs: 150% is 9.3 units, 160% 8.75. Only
 * the travel per tick changes: lockouts, holds and the list ramps count ticks and time. wheel_units
 * is 0 until read, which wheel_sample takes as 50%; one word, so the encoder thread reads it whole.
 */
#define WHEEL_FINE 16
static volatile int wheel_units __attribute__((section(".scratch")));
static int wheel_percent __attribute__((section(".scratch"))),
    wheel_read __attribute__((section(".scratch")));
static void wheel_apply(int pct) {
    wheel_percent = pct;
    wheel_units = (WHEEL_TICK_UNITS * WHEEL_FINE * 50 + pct / 2) / pct;
}
int wheel_pct(void) {
    if (!wheel_read) {
        char v[256] = "", canonical[16];
        toolsReadConfig("/mnt/data/config.ini", "Q2POD", "WHEELSENSITIVITY", v, "");
        int n = atoi(v);
        tk_snprintf(canonical, sizeof canonical, "%d", n);
        wheel_apply(n >= WHEEL_PCT_MIN && n <= 200 && !(n % 10) && !strcmp(v, canonical) ? n : 50);
        wheel_read = 1;
    }
    return wheel_percent;
}
/* The nearest ten within 10-200%, live on the next sample; crossfade.c's refresh saves it. */
void wheel_set_pct(int n) {
    n = n < WHEEL_PCT_MIN ? WHEEL_PCT_MIN : n > 200 ? 200 : (n + 5) / 10 * 10;
    if (n != wheel_pct()) wheel_apply(n);
}

/* Volume guard (Power, Q2POD VOLGUARD, off by default; navigation.c's pod rows set wheel_guard):
 * screen off, a pocket brush must not move the volume (#46). A touch's travel one way counts in
 * wh_guard (1/16 unit) and ticks nothing; past WHEEL_DARK_GUARD the wheel is open (DARK_OPEN) and
 * ticks as lit, reversals too, across lifts. A reversal or a lift before then, or WHEEL_DARK_IDLE_MS
 * without travel, arms it again; so does the screen on. The guard's travel is dropped, not ticked:
 * it is the dead zone, so a brush past it moves the volume by its excess only. */
#define DARK_OPEN (WHEEL_DARK_GUARD * WHEEL_FINE)
int wheel_guard __attribute__((section(".scratch")));
static int wh_guard __attribute__((section(".scratch")));
static unsigned wh_dark_at __attribute__((section(".scratch"))); /* the last dark travel */
void wheel_guard_reset(void) { wh_guard = 0; }
static int dark_held(int *delta) {
    int d = *delta;
    if (g_backlight_status || !wheel_guard) return wh_guard = 0;
    unsigned now = (unsigned)time_now_ms();
    if (now - wh_dark_at >= WHEEL_DARK_IDLE_MS || (d && wh_guard != DARK_OPEN && (d ^ wh_guard) < 0))
        wh_guard = 0;
    if (d) wh_dark_at = now;
    if (wh_guard == DARK_OPEN) return 0;
    wh_acc = 0;
    wh_guard += d;
    if (wh_guard >= DARK_OPEN || wh_guard <= -DARK_OPEN) {
        *delta = wh_guard > 0 ? wh_guard - DARK_OPEN : wh_guard + DARK_OPEN;
        wh_guard = DARK_OPEN;
        return 0;
    }
    return 1;
}

static void wheel_post(char *info, int key) {
    /* Read unlocked: off by one at worst, and the headroom covers it. */
    unsigned short *q = *(unsigned short **)(*(char **)(info + WHEEL_INFO_LOOP) + LOOP_QUEUE);
    int used = q[2] ? q[3] : (q[1] - q[0] + q[3]) % q[3];
    if (q[3] - used < WHEEL_QUEUE_FREE + 2) return;
    for (int type = 0x110; type <= 0x114; type += 4) { /* key down, then up */
        *(int *)(info + WHEEL_INFO_KEY) = key;
        *(int *)(info + WHEEL_INFO_EVENT) = type;
        ((void (*)(void *))WHEEL_POST)(info);
    }
}
/* The wheel's thread and the threads reading it share these, a word each. wheel_frame_at: its last
 * frame with pressure. wh_tick_at, wh_since: its last tick wanted, and how many since Return armed
 * the stop (wh_armed). wheel_mute: the stop, below. */
volatile unsigned wheel_frame_at __attribute__((section(".scratch")));
static volatile unsigned wh_tick_at __attribute__((section(".scratch"))),
    wh_since __attribute__((section(".scratch"))), wh_armed_at __attribute__((section(".scratch")));
static volatile int wh_armed __attribute__((section(".scratch")));
volatile int wheel_mute __attribute__((section(".scratch")));
#if Q2POD_DEV
/* ponytail: temporary, --dev builds only, comes out once the runaway report is explained. The last
 * WL_FRAMES ring frames in RAM, a store per frame; a copy goes to /mnt/data/q2-wheel.log, synced to
 * outlast a forced restart, when Return stops a run, a run passes WL_RUN_MS with no WHEEL_FRESH_MS gap
 * or tick_gate starts dropping: WL_FLUSHES a boot, never past WL_MAX_BYTES, one writer at a time (a
 * flush while one writes is lost). The next boot moves it to the card (tools/build.py WHEEL_LOG_HOOK). */
#define WL_FRAMES 64
#define WL_RUN_MS 30000
#define WL_FLUSHES 8
#define WL_MAX_BYTES 32768
typedef struct {
    int x, pressure;
    unsigned ms;
} wl_frame;
static wl_frame wl_frames[WL_FRAMES] __attribute__((section(".scratch"))),
    wl_copy[WL_FRAMES] __attribute__((section(".scratch")));
static unsigned wl_next __attribute__((section(".scratch"))),
    wl_copy_next __attribute__((section(".scratch"))),
    wl_copy_at __attribute__((section(".scratch"))), wl_flushes __attribute__((section(".scratch"))),
    wl_run_at __attribute__((section(".scratch"))), wl_drop_at __attribute__((section(".scratch"))),
    wl_stops __attribute__((section(".scratch"))), wl_drops __attribute__((section(".scratch")));
static int wl_run __attribute__((section(".scratch"))); /* 0 none, 1 going, 2 logged */
static int wl_busy __attribute__((section(".scratch")));
static const char *wl_why __attribute__((section(".scratch")));
static void *wl_write(void *unused) {
    void *f = fopen("/mnt/data/q2-wheel.log", "ab");
    long size = f && !fseek(f, 0, 2) ? ftell(f) : -1;
    if (size >= 0 && size < WL_MAX_BYTES) {
        char s[80];
        tk_snprintf(s, sizeof s, "== wheel: %s at %u ms: gate drops %u, stops %u\n", wl_why,
                    wl_copy_at, wl_drops, wl_stops);
        fputs(s, f);
        for (unsigned i = 0; i < WL_FRAMES; ++i) {
            const wl_frame *k = &wl_copy[(wl_copy_next + i) % WL_FRAMES];
            if (!k->ms) continue; /* not filled yet */
            tk_snprintf(s, sizeof s, "%u x=%d p=%d\n", k->ms, k->x, k->pressure);
            fputs(s, f);
        }
        fflush(f);
        fsync(fileno(f));
    }
    if (f) fclose(f);
    wl_busy = 0;
    return unused;
}
/* Any thread. The copy may catch one frame half written, which a log can bear. */
static void wl_flush(const char *why) {
    unsigned long thread;
    if (!__sync_bool_compare_and_swap(&wl_busy, 0, 1)) return;
    if (wl_flushes >= WL_FLUSHES) {
        wl_busy = 0;
        return;
    }
    wl_flushes++;
    wl_why = why;
    wl_copy_next = wl_next;
    wl_copy_at = (unsigned)time_now_ms();
    memcpy(wl_copy, wl_frames, sizeof wl_copy);
    if (pthread_create(&thread, 0, wl_write, 0))
        wl_busy = 0;
    else
        pthread_detach(thread);
}
/* tick_gate, for a tick with no frame: a run of them leaves a line that tells it from a ghost. */
void wheel_gate_note(void) {
    unsigned now = (unsigned)time_now_ms();
    wl_drops++;
    if (now - wl_drop_at >= WHEEL_FRESH_MS) wl_flush("a tick with no ring frame");
    wl_drop_at = now;
}
static void wl_note_frame(const int *s) {
    wl_frame *k = &wl_frames[wl_next++ % WL_FRAMES];
    k->x = s[0], k->pressure = s[2], k->ms = (unsigned)time_now_ms();
    if (!s[2]) wl_run = 0;
}
static void wl_note_stop(void) {
    wl_stops++;
    wl_run = 2;
    wl_flush("Return stopped a run");
}
static void wl_note_tick(unsigned now, unsigned before) {
    if (!wl_run || now - before >= WHEEL_FRESH_MS) wl_run = 1, wl_run_at = now;
    if (wl_run == 1 && now - wl_run_at >= WL_RUN_MS) {
        wl_run = 2;
        wl_flush("a run past 30 s");
    }
}
#else
#define wl_note_frame(s) ((void)0)
#define wl_note_stop() ((void)0)
#define wl_note_tick(now, before) ((void)(before))
#endif

/* main_loop_queue_event (tools/build.py QUEUE_LEAF): the one way into the UI loop's queue, for every
 * input thread, SyncLink's keys and idle requests alike; full, it drops its oldest event. The touch
 * thread (tslib_run, 0x635c50) posts an event of type 0 for every sample with no pressure while it
 * holds no finger down, which the loop hands the window manager as EVT_NONE. Twenty of them would
 * evict a queued release, and the wheel and Centre were dead until a tap (inferred; q2-wheel.log
 * showed thread=0, last=move). They mean nothing, so they are not queued; everything else goes to
 * stock's poster.
 * Return's stop. A first Return only does its normal job: a thumb can slide from the ring to Return and
 * back with no lift, and a turn begun on Back carries on. One past WHEEL_ARM_MS after it is a new first.
 * A second one with WHEEL_STOP_TICKS ticks since the first, and ticks and frames still arriving, stops the ticks until a
 * real lift or a WHEEL_FRESH_MS gap in frames (wheel_sample), and tick_gate refuses those queued. So
 * under a ghost the first Return closes the volume bar or goes up, and the second ends it. Here, at
 * the press, so a stalled UI doesn't move it. ponytail: a real turn with two rim presses within
 * WHEEL_ARM_MS in one touch stops until its lift; nothing else ever limits a turn. */
int ringnav_queue_event(void *loop, const unsigned *r) {
    int (*post)(void *, const void *) =
        loop ? *(int (**)(void *, const void *))((char *)loop + LOOP_POST) : 0;
    if (!post || !r) return 0x10; /* RET_BAD_PARAMS, as stock */
    if (r[0] == EVT_KEY_DOWN && r[EVENT_KEY / 4] == KEY_RETURN && wh_anchor && !wheel_mute) {
        unsigned now = (unsigned)time_now_ms();
        if (!wh_armed || (int)(now - wh_armed_at) >= WHEEL_ARM_MS) {
            wh_since = 0;
            wh_armed = 1;
            wh_armed_at = now;
        } else if (wh_since >= WHEEL_STOP_TICKS && (int)(now - wh_tick_at) < WHEEL_ESCAPE_TICK_MS &&
                   (int)(now - wheel_frame_at) < WHEEL_FRESH_MS) {
            wheel_mute = 1;
            wl_note_stop();
        }
    }
    return r[0] ? post(loop, r) : 0;
}

volatile int wheel_raw __attribute__((section(".scratch")));
static int wh_raw __attribute__((section(".scratch")));
volatile int wheel_travel __attribute__((section(".scratch")));
volatile int wheel_held __attribute__((section(".scratch"))); /* a finger on the wheel */

/* struct ts_sample {int x, y; unsigned pressure; struct timeval tv;} */
void wheel_sample(int *s, int ret, char *info) {
    if (ret <= 0) return;
    wl_note_frame(s); /* x as it came, before anything judges it */
    if (!s[2]) { /* lifted */
        /* Stock's lift path (0x638a68) ticks on an x away from its touch-down x, 0 once it has seen
         * this touch's lift, per sample and past wheel_post's guard: it only ever sees that x. */
        s[0] = wh_anchor;
        wh_anchor = 0;
        wheel_mute = wh_armed = 0;
        if (wh_guard != DARK_OPEN) wh_guard = 0;
        wheel_held = 0;
        return;
    }
    unsigned now = (unsigned)time_now_ms();
    if (now - wheel_frame_at >= WHEEL_FRESH_MS) wheel_mute = wh_armed = 0;
    wheel_frame_at = now;
    if (!wh_anchor) {
        wh_anchor = wh_last = s[0];
        wh_acc = 0;
        wh_raw = 0;
        wheel_held = 1;
        return;
    }
    int d = s[0] - wh_last, units = wheel_units ? wheel_units : WHEEL_TICK_UNITS * WHEEL_FINE;
    d = d < -100 ? d + 200 : d > 100 ? d - 200 : d;
    d *= WHEEL_FINE;
    wh_last = s[0];
    if (wheel_raw) { /* games.c's Vortex reads the travel itself; nothing is queued for a menu */
        __sync_fetch_and_add(&wheel_travel, d / WHEEL_FINE);
        wh_acc = 0;
        /* Cover Flow's thumb (2): its travel counts as ticks towards Return's stop */
        if (wheel_raw == WHEEL_THUMB)
            for (wh_raw += d < 0 ? -d : d; wh_raw >= units; wh_raw -= units) {
                wh_tick_at = now;
                wh_since++;
            }
        s[0] = wh_anchor;
        return;
    }
    /* Sensitivity changed mid-turn (its own page takes the wheel): the carry keeps its share of a
     * tick, so it stays under the new travel and fires no burst. */
    if (units != wh_units) {
        wh_acc = wh_units ? wh_acc * units / wh_units : 0;
        wh_units = units;
    }
    if (d && (d ^ wh_acc) < 0) wh_acc = 0; /* reversed */
    wh_acc = dark_held(&d) ? 0 : wh_acc + d;
    while (wh_acc >= units || wh_acc <= -units) {
        int dir = wh_acc > 0 ? 1 : -1;
        unsigned before = wh_tick_at;
        wh_acc -= dir * units;
        wh_tick_at = now;
        wh_since++;
        wl_note_tick(now, before);
        if (!wheel_mute) wheel_post(info, dir > 0 ? KEY_PREV : KEY_NEXT); /* x rising is back, as stock */
    }
    s[0] = wh_anchor;
}

/* $t9 still holds ts_read, which stock loaded for its jalr; $s1 is the thread's info, which stock
 * hands its poster. tslib_thread_run's touch screen reads stay stock. */
__asm__(".set noreorder\n.globl ringnav_ts_read\nringnav_ts_read:\n"
        "addiu $sp, $sp, -32\nsw $ra, 28($sp)\nsw $a1, 20($sp)\n"
        "jalr $t9\nnop\n"
        "sw $v0, 16($sp)\nlw $a0, 20($sp)\nmove $a1, $v0\n"
        "jal wheel_sample\nmove $a2, $s1\n"
        "lw $v0, 16($sp)\nlw $ra, 28($sp)\njr $ra\naddiu $sp, $sp, 32\n.set reorder\n");
static void *queue(void) { return P(mcl_pdeqplaylist, 0); }
static void close_preload(void) {
    if (M(MCL_PRELOAD) == 1 && M(MCL_FD) != -1) send(M(MCL_FD), "{mcl-closegapless\\null}", 23, 0);
    M(MCL_PRELOAD) = 0;
    M(MCL_PREPOS) = -1;
}

/* Full parent paths, not album tags or browsing context: a mixed queue keeps each book's speed.
 * The path stored beside the value also rejects a hash collision or a damaged preference. */
typedef struct {
    char folder[1024];
    unsigned mode;
} folder_speed;

static int speed_folder(int at, char folder[1024]) {
    void *q = queue();
    if (!q || at < 0 || (unsigned)at >= deque_size(q)) return 0;
    void *r = deque_at(q, (unsigned)at);
    const char *p = r ? P(r, REC_PATH) : (void *)0;
    if (!p || I(r, REC_TYPE) != 8 || *p != '/') return 0;
    const char *end = strrchr(p, '/');
    unsigned n = (unsigned)(end - p) + 1;
    if (n >= 1024 || !end[1]) return 0;
    memcpy(folder, p, n);
    folder[n] = 0;
    return 1;
}

static void speed_file(char path[80], const char *folder) {
    tk_snprintf(path, 80, "/mnt/data/ringnav-speed-%08x", fnv(FNV_SEED, (const unsigned char *)folder));
}

static unsigned speed_read(int at) {
    char folder[1024], path[80];
    folder_speed saved;
    if (!speed_folder(at, folder)) return 0;
    speed_file(path, folder);
    return blob_io(path, path, &saved, sizeof saved, 0) && saved.folder[1023] == 0 &&
                   !strcmp(saved.folder, folder) && saved.mode < 4
               ? saved.mode
               : 0;
}

void playback_speed_start(void) { g_playspeed_mode = speed_read(M(MCL_POS)); }
/* A track's start: its folder's speed, sent to the decoder, which keeps the last track's otherwise. */
void playback_track_speed(void) {
    playback_speed_start();
    stock_speed_trampoline(g_playspeed_mode);
}

int ringnav_speed(int mode) {
    folder_speed saved = { { 0 }, 0 };
    char path[80], tmp[84];
    /* player_initconfig re-applies speed when hciplayer reconnects after a disconnect; not a user edit. */
    if (__builtin_return_address(0) == (void *)0x51e04c || (unsigned)mode >= 4) {
        playback_speed_start();
        return stock_speed_trampoline(g_playspeed_mode);
    }
    if (!speed_folder(M(MCL_POS), saved.folder)) { /* no folder (a Tidal stream): this track only */
        g_playspeed_mode = (unsigned char)mode;
        close_preload();
        return stock_speed_trampoline(mode);
    }
    saved.mode = (unsigned)mode;
    speed_file(path, saved.folder);
    tk_snprintf(tmp, sizeof tmp, "%s.tmp", path);
    if (!blob_io(path, tmp, &saved, sizeof saved, 1)) {
        unlink(tmp);
        playback_speed_start();
        toast("Could not save folder speed");
        return stock_speed_trampoline(g_playspeed_mode);
    }
    g_playspeed_mode = (unsigned char)mode;
    close_preload(); /* an already-open decoder still has the previous speed */
    return stock_speed_trampoline(mode);
}

/* A local track whose MP4 index would not fit in RAM (mp4_fits) is never opened: the decoder
 * would take the whole device down with it, at boot again through Memory Playback. */
static const char *track_path(int at) {
    void *q = queue();
    void *r = q && at >= 0 && (unsigned)at < deque_size(q) ? deque_at(q, (unsigned)at) : 0;
    return r && I(r, REC_TYPE) == 8 ? P(r, REC_PATH) : 0;
}
static int too_long; /* set where mclStartPlayer runs, maybe off the UI thread; toasted by the poll */
/* The file the player holds: the last one started. A start closes it before it opens the next, yet
 * MemAvailable still counts its index here. The same file again (a CUE book's next chapter) would
 * need room for two, so it starts unchecked; another book is checked with that index given back
 * (mp4_start_fits). A preload, which opens beside the playing file, is checked in full.
 * ponytail: RAM taken since that file's start is not counted; a boot's first start always checks. */
static char started[1024];
int playback_start_fits(void) {
    const char *path = track_path(M(MCL_POS));
    if (path && strcmp(path, started) && !mp4_start_fits(path, started)) {
        too_long = 1;
        return 0;
    }
    tk_snprintf(started, sizeof started, "%s", path ? path : ""); /* a stream: the book is closed */
    return 1;
}

int ringnav_open_preload(void) {
    /* A gapless handoff does not run player_initconfig: reopen at a speed boundary. A track too
     * long to open goes through mclStartPlayer, whose hook declines it. */
    const char *path = track_path(M(MCL_PREPOS));
    if (speed_read(M(MCL_PREPOS)) != g_playspeed_mode || (path && !mp4_fits(path))) {
        M(MCL_PRELOAD) = -1; /* stock's failed attempt: do not rescan on every auto-change tick */
        return 1;
    }
    return stock_open_preload_trampoline();
}
static void release(void) {
    s.next_valid = 0;
    if (s.order) free(s.order);
    if (s.cycle) free(s.cycle);
    if (s.groups) free(s.groups);
    if (s.seen) free(s.seen);
    if (s.history) free(s.history);
    if (ids) free(ids);
    ids = 0;
    s.order = s.cycle = 0;
    s.groups = s.seen = s.history = 0;
    s.n = s.hn = s.cursor = s.npend = 0;
    s.resume_key = s.resume_pending = s.resume_wait = s.save_failed = 0;
    unlink(QUEUE_FILE);
    s.dirty = 1; /* the next save writes a plain snapshot whole */
}
/* Albums as Coverflow tells them apart (album_cmp: tagged albums first), or folders. */
static int group_cmp(void *a, void *b) { return s.folder ? folder_cmp(a, b) : album_cmp(a, b); }
/* Total order matching group_cmp, then index. */
static int group_before(const void *pa, const void *pb) {
    unsigned ia = ((const entry *)pa)->index, ib = ((const entry *)pb)->index;
    int d = group_cmp(deque_at(queue(), ia), deque_at(queue(), ib));
    return d ? d : ia < ib ? -1 : ia != ib;
}
static int compare(const void *pa, const void *pb) {
    const entry *a = pa, *b = pb;
    if (a->group != b->group) return a->group < b->group ? -1 : 1;
    if (a->rank != b->rank) return a->rank < b->rank ? -1 : 1;
    void *ra = deque_at(queue(), a->index), *rb = deque_at(queue(), b->index);
    int d = s.folder ? strcmp(P(ra, REC_PATH), P(rb, REC_PATH)) : album_before(&ra, &rb);
    if (!d) d = I(ra, REC_CUE_START) - I(rb, REC_CUE_START);
    return d ? d : a->index < b->index ? -1 : a->index != b->index;
}
/* xorshift32, seeded once from stock's generator and the tick clock: stock's alone repeats
 * itself between shuffles started close together. */
static unsigned rng __attribute__((section(".scratch")));
unsigned rand_below(unsigned n) {
    if (!rng) rng = ((unsigned)toolsRandnum(0x7fffffff) ^ time_now_ms() * 2654435761u) | 1;
    rng ^= rng << 13;
    rng ^= rng >> 17;
    rng ^= rng << 5;
    return rng % n;
}
static void shuffle(entry *v, unsigned n) {
    for (unsigned i = n; i > 1; --i) {
        unsigned j = rand_below(i);
        entry t = v[i - 1];
        v[i - 1] = v[j];
        v[j] = t;
    }
}
static void make_order(entry *v) {
    for (unsigned i = 0; i < s.n; ++i) v[i] = (entry){ i, s.groups[i], 0 };
    if (s.shuffle == 1) { /* no sort first: the shuffle undoes it, seconds on a big library */
        shuffle(v, s.n);
        return;
    }
    qsort(v, s.n, sizeof *v, compare);
    if (s.shuffle == 2 || s.shuffle == 4)
        for (unsigned i = 0, j; i < s.n; i = j) {
            for (j = i + 1; j < s.n && v[j].group == v[i].group; ++j) {}
            shuffle(v + i, j - i);
        }
    if (s.shuffle == 3 || s.shuffle == 4) {
        /* Rank groups with a shuffled permutation, so even random ties cannot split a group. */
        for (unsigned i = 0; i < s.n; ++i) s.cycle[i].rank = i;
        for (unsigned i = s.n; i > 1; --i) {
            unsigned j = rand_below(i), t = s.cycle[i - 1].rank;
            s.cycle[i - 1].rank = s.cycle[j].rank;
            s.cycle[j].rank = t;
        }
        for (unsigned i = 0; i < s.n; ++i) v[i].group = s.cycle[s.groups[v[i].index]].rank;
        /* Stable group sort: rank is the within-group order just generated. */
        for (unsigned i = 0; i < s.n; ++i) v[i].rank = i;
        qsort(v, s.n, sizeof *v, compare);
    }
}
static void history(unsigned at) {
    /* ponytail: keep the last 4096 transitions; use an unbounded disk log if more is needed. */
    if (s.hn == HISTORY_LIMIT) {
        for (unsigned i = 1; i < s.hn; ++i) s.history[i - 1] = s.history[i];
        s.resume_key = s.resume_pending = 0;
        --s.hn;
    }
    s.history[s.hn++] = at;
}
static unsigned rec_id(unsigned i) {
    void *r = deque_at(queue(), i);
    return hash_bytes(fnv(FNV_SEED, P(r, REC_PATH)), (const unsigned char *)r + REC_CUE_START, 4);
}
static int rebuild(int keep) {
    s.next_valid = 0;
    unsigned n = queue() ? deque_size(queue()) : 0;
    if (n > QUEUE_LIMIT) { /* before the tables below take 36 B a song */
        playback_clear();
        return 0;
    }
    unsigned *oldseen = s.seen, *oldhistory = s.history;
    unsigned oldn = s.n, oldhn = s.hn;
    entry *order = calloc(n + 1, sizeof *order), *cycle = calloc(n + 1, sizeof *cycle);
    unsigned *groups = calloc(n + 1, sizeof *groups), *seen = calloc(n + 1, sizeof *seen),
             *hist = calloc(HISTORY_LIMIT, sizeof *hist), *id = calloc(n + 1, sizeof *id);
    if (!order || !cycle || !groups || !seen || !hist || !id) {
        free(order);
        free(cycle);
        free(groups);
        free(seen);
        free(hist);
        free(id);
        playback_clear(); /* every caller ends advanced play: no older queue's snapshot stays */
        return 0;
    }
    if (s.order) free(s.order);
    if (s.cycle) free(s.cycle);
    if (s.groups) free(s.groups);
    if (ids) free(ids);
    for (unsigned i = 0; i < n; ++i) id[i] = rec_id(i);
    ids = id;
    s.order = order;
    s.cycle = cycle;
    s.groups = groups;
    s.seen = seen;
    s.history = hist;
    s.n = n;
    s.hn = keep ? oldhn : 0;
    if (keep) {
        memcpy(hist, oldhistory, oldhn * sizeof *hist);
        memcpy(seen, oldseen, (oldn < n ? oldn : n) * sizeof *seen);
    }
    if (oldseen) free(oldseen);
    if (oldhistory) free(oldhistory);
    /* Sort by group_cmp; each run's group is its smallest index. cycle is scratch here. */
    for (unsigned i = 0; i < n; ++i) cycle[i].index = i;
    qsort(cycle, n, sizeof *cycle, group_before);
    for (unsigned i = 0, first = 0; i < n; ++i) {
        if (!i || group_cmp(deque_at(queue(), cycle[i - 1].index), deque_at(queue(), cycle[i].index)))
            first = cycle[i].index;
        groups[cycle[i].index] = first;
    }
    make_order(order);
    unsigned at = (unsigned)M(MCL_POS), k = 0;
    if (at < n) {
        if (!keep) history(at);
        if (s.shuffle == 1) {
            /* Shuffle All pins the current occurrence between visited and remaining entries. */
            for (unsigned i = 0; i < n; ++i)
                if (order[i].index != at && seen[order[i].index]) cycle[k++] = order[i];
            s.cursor = k;
            cycle[k++] = (entry){ at, groups[at], 0 };
            for (unsigned i = 0; i < n; ++i)
                if (order[i].index != at && !seen[order[i].index]) cycle[k++] = order[i];
            memcpy(order, cycle, n * sizeof *order);
        } else {
            if (s.shuffle >= 3) {
                /* Finish the current category before the other shuffled categories; keep its
                 * track order, even when the playing track is halfway through that category. */
                for (unsigned i = 0; i < n; ++i)
                    if (groups[order[i].index] == groups[at]) cycle[k++] = order[i];
                for (unsigned i = 0; i < n; ++i)
                    if (groups[order[i].index] != groups[at]) cycle[k++] = order[i];
                memcpy(order, cycle, n * sizeof *order);
            }
            if (s.shuffle == 2 || s.shuffle == 4) {
                unsigned first = 0, current = 0;
                while (first < n && groups[order[first].index] != groups[at]) ++first;
                while (current < n && order[current].index != at) ++current;
                entry playing = order[current];
                for (; current > first; --current) order[current] = order[current - 1];
                order[first] = playing;
            }
            for (unsigned i = 0; i < n; ++i)
                if (order[i].index == at) {
                    s.cursor = i;
                    break;
                }
            for (unsigned i = 0; i < s.cursor; ++i) seen[order[i].index] = 1;
        }
        seen[at] = 1;
    }
    /* Generate the following cycle on commit, never in the preload/peek path. */
    make_order(cycle);
    s.dirty = 1;
    return 1;
}
/* Stock's own queue edit (View Queue's delete) takes entries out behind advanced play, so its
 * indices name other tracks or none. On the UI thread they follow: the entries left keep their seen
 * marks, history and Play Next places, shifted down as playback_insert shifts them up, and the order
 * is drawn again over them, as an insert's is. Anything but a deletion (or no memory) takes a new
 * order over the queue, as a new load gets, Play Next and the history gone. Off the UI thread
 * (headset keys, a natural change) it returns 1 and the caller takes stock's way this once; the
 * next UI pass follows. Only a size change is seen: a delete always makes one. */
static int follow_queue(void) {
    unsigned m = queue() ? deque_size(queue()) : 0, j = 0, k = 0;
    if (!s.active || m == s.n) return 0;
    if (pthread_self() != ui_thread) return 1;
    close_preload();
    unsigned *map = ids ? calloc(s.n + 1, sizeof *map) : 0, *seen = calloc(m + 1, sizeof *seen);
    for (unsigned i = 0; map && i < s.n; ++i) map[i] = j < m && rec_id(j) == ids[i] ? j++ : ~0u;
    if (!map || !seen || j != m) {
        free(map);
        free(seen);
        s.npend = 0;
        s.active = rebuild(0);
        return 0;
    }
    for (unsigned i = 0; i < s.n; ++i)
        if (map[i] != ~0u) seen[map[i]] = s.seen[i];
    for (unsigned i = 0; i < s.hn; ++i)
        if (s.history[i] < s.n && map[s.history[i]] != ~0u) s.history[k++] = map[s.history[i]];
    s.hn = k;
    k = 0;
    for (unsigned i = 0; i < s.npend; ++i)
        if (s.pend[i] < s.n && map[s.pend[i]] != ~0u) s.pend[k++] = map[s.pend[i]];
    s.npend = k;
    free(map);
    free(s.seen);
    s.seen = seen;
    s.n = m;
    unsigned at = (unsigned)M(MCL_POS);
    /* the playing entry gone, stock's position names the one after it: Previous comes back here */
    if (at < m && (!s.hn || s.history[s.hn - 1] != at)) history(at);
    s.active = rebuild(1);
    return 0;
}
int playback_option(int kind) {
    return kind == 0 ? (s.active ? s.shuffle : 0) : kind == 1 ? (s.active ? s.repeat : 2) : s.folder;
}
int playback_set(int kind, int value) {
    if (kind < 0 || kind > 2 || value < 0 || value >= (kind == 0 ? 5 : kind == 1 ? 6 : 2)) return 0;
    if (s.active && playback_option(kind) == value) return 1;
    if (!s.active) {
        s.repeat = 2;
        s.shuffle = 0;
        s.folder = 0;
    }
    if (kind == 0)
        s.shuffle = value;
    else if (kind == 1)
        s.repeat = value;
    else
        s.folder = value;
    close_preload();
    s.npend = 0;
    s.active = rebuild(s.active);
    playback_save();
    return s.active;
}
int playback_groups(void *all, int folder) {
    if (!deque_size(all)) return 0;
    mclLoadPlayList(all, 0, 0xf001);
    s.folder = folder;
    s.shuffle = 3;
    s.repeat = 2;
    s.active = rebuild(0);
    if (!s.active) return 0;
    make_order(s.order);
    int at = (int)s.order[0].index;
    s.grouping = 1; /* the load play_folder starts must not end the groups it was started for */
    play_folder(all, at, 0xf001);
    s.grouping = 0;
    playback_save();
    return 1;
}
/* The mode Shuffle Songs replaces: the one it already would restore, else the current one. Taken
 * before its own mode change and load, so neither forgets nor restores it. */
int playback_unshuffle(void) {
    int mode = unshuffle ? unshuffle - 1 : M(MCL_MODE);
    unshuffle = 0;
    return mode;
}
/* Shuffle Songs: stock's shuffle draws each next at random, so it has no order for Up Next. Take
 * the loaded queue over as advanced Shuffle Songs, Repeat All, as stock's never ends. */
int playback_shuffle(int mode) {
    unshuffle = mode == 2 ? 0 : mode + 1;
    s.shuffle = 1;
    s.repeat = 5;
    s.folder = 0;
    s.npend = 0;
    close_preload();
    s.active = rebuild(0);
    playback_save();
    return s.active;
}
/* View Queue: 1 if a shuffle's play order is up, which Up Next lists rather than stock's queue
 * order. Stock Shuffle drawing each next at random has none, so it becomes Shuffle Songs here
 * (playback_shuffle, the mode to restore kept). Advanced play without a shuffle plays in queue
 * order, as stock's list shows it. ponytail: the new order is fresh, so songs stock's shuffle already
 * played can come round again; mark those missing from MCL_POOL seen if that matters. */
int playback_listable(void) {
    if (s.active) return s.shuffle != 0;
    return M(MCL_MODE) == 2 && playback_shuffle(playback_unshuffle());
}
/* The pending Play Next list: queue indices, the head plays first. */
static unsigned pend_find(unsigned x) {
    unsigned k = 0;
    while (k < s.npend && s.pend[k] != x) ++k;
    return k;
}
/* x to the front, out of its old place if listed; a full list drops its last entry. */
static void pend_front(unsigned x) {
    unsigned k = pend_find(x);
    if (k == s.npend) {
        if (s.npend == PEND_LIMIT) --s.npend;
        k = s.npend++;
    }
    for (; k > 0; --k) s.pend[k] = s.pend[k - 1];
    s.pend[0] = x;
}
/* Takes x off the list if listed; returns whether it was. */
static int pend_take(unsigned x) {
    unsigned k = pend_find(x);
    if (k == s.npend) return 0;
    for (--s.npend; k < s.npend; ++k) s.pend[k] = s.pend[k + 1];
    return 1;
}
/* The indices that play after this track, in order, ignoring the single-song rules. Peek has no
 * side effects, RNG or history writes. */
static unsigned upcoming(unsigned *out, unsigned max) {
    unsigned at = (unsigned)M(MCL_POS), k = 0;
    if (!s.active || at >= s.n) return 0;
    int category = s.repeat == 1 || s.repeat == 4;
    /* Play Next's list first, in order; the order walk below skips its members */
    for (unsigned j = 0; j < s.npend && k < max; ++j) {
        unsigned x = s.pend[j];
        if (!category || s.groups[x] == s.groups[at]) out[k++] = x;
    }
    for (unsigned i = s.cursor + 1; i < s.n && k < max; ++i) {
        unsigned x = s.order[i].index;
        if (!s.seen[x] && pend_find(x) == s.npend && (!category || s.groups[x] == s.groups[at]))
            out[k++] = x;
    }
    if (s.repeat < 3) return k;
    /* the next pass after the order runs out: the cycle, wrapped */
    for (unsigned i = 0; i < s.n && k < max; ++i) {
        unsigned x = s.cycle[i].index;
        if (!category || s.groups[x] == s.groups[at]) out[k++] = x;
    }
    return k;
}
/* Up Next: what an automatic next plays after this track. Repeat Song and Play One Song have none,
 * as a natural end has none. */
unsigned playback_upcoming(unsigned *out, unsigned max) {
    return s.repeat == 0 || s.repeat == 3 ? 0 : upcoming(out, max);
}
/* Manual next bypasses single-song rules. */
int playback_successor(int automatic) {
    unsigned at = (unsigned)M(MCL_POS), x;
    if (!automatic) return upcoming(&x, 1) ? (int)x : -1;
    if (!s.next_valid || s.next_pos != at) {
        s.next_value = s.repeat == 0                           ? -1
                       : s.repeat == 3 && s.active && at < s.n ? (int)at
                       : upcoming(&x, 1)                       ? (int)x
                                                               : -1;
        s.next_pos = at;
        s.next_valid = 1;
    }
    return s.next_value;
}
/* A Play Next pick plays now: its order entry moves immediately after the cursor, leaving other
 * order intact, even when the order sorted it before the cursor (already marked seen). */
static void play_now(unsigned next) {
    unsigned i = 0;
    while (i < s.n && s.order[i].index != next) ++i;
    if (i < s.cursor) {
        entry t = s.order[i];
        for (; i < s.cursor; ++i) s.order[i] = s.order[i + 1];
        s.order[i] = t;
        --s.cursor;
    } else if (i > s.cursor && i < s.n) {
        entry t = s.order[i];
        for (; i > s.cursor + 1; --i) s.order[i] = s.order[i - 1];
        s.order[i] = t;
    }
    s.seen[next] = 0;
}
static void commit(unsigned next) {
    s.resume_key = s.resume_pending = 0;
    if (pend_take(next)) play_now(next);
    unsigned i = s.cursor + 1;
    while (i < s.n && s.order[i].index != next) ++i;
    if ((i == s.n || s.seen[next]) && !(s.repeat == 3 && next == (unsigned)M(MCL_POS))) {
        memcpy(s.order, s.cycle, s.n * sizeof *s.order);
        memset(s.seen, 0, s.n * sizeof *s.seen);
        make_order(s.cycle);
        for (i = 0; i < s.n && s.order[i].index != next; ++i) {}
    }
    if (i < s.n) s.cursor = i;
    s.seen[next] = 1;
    history(next);
    s.next_valid = 0;
    s.dirty = 1;
}
/* player_start_queue: stock's View Queue row (and its queue delete, DLNA and URL plays) start queue
 * index at. In advanced play the pick is committed first: a shuffle's through Play Next's path, so
 * the order carries on after it and it neither plays again later in the pass nor drops the tracks
 * it jumped; an ordered queue's in place, so the tracks after it follow. A pick inside a pending
 * Play Next takes only its entry off the list, the rest stay pending. */
#define VIEW_QUEUE_RET 0x53acf8 /* playerqueue_row_on_click: View Queue's row, past its checks */
int ringnav_start_queue(int at) {
    unsigned x = (unsigned)at;
    follow_queue();
    if (__builtin_return_address(0) == (void *)VIEW_QUEUE_RET && x == (unsigned)M(MCL_POS) &&
        playback_keeps(queue(), at)) {
        playback_np();
        return 1;
    }
    if (s.active && x < s.n && x != (unsigned)M(MCL_POS) && queue() && deque_size(queue()) == s.n) {
        close_preload();
        if (s.shuffle == 1 && pend_find(x) == s.npend) play_now(x);
        commit(x);
    }
    return stock_start_queue_trampoline(at);
}
/* A video Folder view lists in the Videos folder (ringnav_audio_extension) plays from its row in
 * q2video, never from a queue: stock gets a copy without them, at moved to the same track (or the
 * next song after a video), and q, the caller's showlist, stays whole. 0 when nothing to drop. */
extern int video_ext(const char *ext);
int playback_video(void *r) {
    const char *path = P(r, REC_PATH);
    return I(r, REC_TYPE) == 8 && path && video_ext(strrchr(path, '.'));
}
static void *no_videos(void *q, int *at) {
    unsigned n = q ? deque_size(q) : 0, keep = 0, before = 0;
    for (unsigned i = 0; i < n; ++i)
        if (!playback_video(deque_at(q, i))) keep++, before += (int)i < *at;
    if (keep == n) return 0;
    void *out = _create_deque("stSongInfo");
    deque_init(out);
    for (unsigned i = 0; i < n; ++i)
        if (!playback_video(deque_at(q, i))) _deque_push_back(out, deque_at(q, i));
    if (!keep) *at = 0;
    else if (*at > 0) *at = (int)(before < keep ? before : keep - 1);
    return out;
}
int ringnav_load(void *q, int at, int type) {
    ++playback_loads;
    app_fav_load(); /* tidal.c: an app song's heart isn't the new queue's */
    if (s.restoring == 2) type = M(MCL_TYPE);
    close_preload();
    /* After Shuffle Songs, before stock loads; ringnav_mode's playback_clear ends its shuffle. */
    if (unshuffle && !s.restoring) config_playmode(playback_unshuffle(), 1);
    void *own = no_videos(q, &at);
    int result = stock_load_trampoline(own ? own : q, at, type);
    if (own) deque_destroy(own);
    back_to = 0;
    if (s.restoring == 2)
        s.restoring = 0;
    else if (!s.restoring) {
        press_stamp(); /* a list pick, after stock's load names the queue's type */
        s.resume_key = s.resume_pending = 0;
        s.npend = 0;
        /* Shuffle Albums/Folders end with any other queue; a repeat or Shuffle track mode carries on */
        if (s.active && (type == 1 || (type & 0xf000) == 0xf000) && (s.shuffle < 3 || s.grouping))
            s.active = rebuild(0);
        else {
            s.active = 0;
            release();
        }
    }
    return result;
}
extern void ringnav_read_artistype(void);
/* config_init applies the saved mode before the Home page can restore the queue; Artists is read
 * here too, as stock reads it only when a Library list or artist page opens, and Night Shift's
 * setting, so no paint reads one (nightshift.c night_paint). */
int ringnav_bootmode(int mode, int notify) {
    peq_boot();
    ringnav_read_artistype();
    night_on();
    s.restoring = 1;
    int result = config_playmode(mode, notify);
    s.restoring = 0;
    return result;
}
void playback_clear(void) {
    /* Stock keeps a preload when selecting List Play; an advanced repeat preload may wrap. */
    if (s.active) close_preload();
    s.active = 0;
    release();
}
int ringnav_mode(int mode) {
    /* player_initconfig re-applies the saved mode when hciplayer reconnects after a disconnect
     * (never on the first connection at boot): not a choice, so advanced play stays. */
    int initconfig = __builtin_return_address(0) == (void *)INITCONFIG_MODE_RET;
    if (initconfig) {
        /* A new hciplayer has no filter: peq_chain attaches again. With the EQ on, player_initconfig's
         * own set_equalizer_value (0x51e068, before this call) has put it in already. */
        if (!g_equalizer_flag) peq_chained = 0;
    }
    if (s.restoring || initconfig) return stock_mode_trampoline(mode);
    unshuffle = 0; /* a mode choice outlasts Shuffle Songs */
    /* Stock Shuffle (2) on Shuffle Albums/Folders keeps the groups; any other mode ends them. */
    if (!(s.active && s.shuffle >= 3 && mode == 2)) playback_clear();
    return stock_mode_trampoline(mode);
}
/* List Play on a library queue's last track. Stock stops there at a natural end, but its manual
 * Next wraps to the first track (player_change_music, without mclNextSong); both stop instead. */
static int list_end(void) {
    return M(MCL_MODE) == 0 && (M(MCL_TYPE) & 0xf000) == 0xf000 && queue() && M(MCL_POS) >= 0 &&
           (unsigned)M(MCL_POS) + 1 == deque_size(queue());
}
unsigned auto_nexts __attribute__((section(".scratch"))); /* tracks that ended by themselves */
static int next_song(int automatic) {
    auto_nexts += automatic != 0;
    if (follow_queue() || !s.active) {
        if (list_end()) {
            close_preload();
            mclStop();
            return -1;
        }
        return stock_next_trampoline(automatic);
    }
    int next = playback_successor(automatic);
    if (next < 0) {
        close_preload();
        mclStop();
        return -1;
    }
    commit((unsigned)next);
    if (automatic && M(MCL_PRELOAD) == 1 && M(MCL_PREPOS) == next)
        return stock_next_trampoline(automatic); /* stock consumes the preloaded decoder */
    close_preload();
    M(MCL_POS) = next;
    return mclStartPlayer();
}
/* A natural change (mclAutoChange's, its only automatic caller) forgets the last press and stamps nothing:
 * a Folder Skip's rollover loads its next folder in here (on_player_autochange, ringnav_load). */
int ringnav_next(int automatic) {
    if (!automatic) return next_song(0);
    press_forget();
    press_natural = 2; /* 2: start_mute's too, 1 being the other natural starts */
    int result = next_song(1);
    press_natural = 0;
    return result;
}
int playback_back(void) {
    int to = back_to - 1;
    back_to = 0;
    return to >= 0 && M(MCL_POS) == back_at && queue() && (unsigned)to < deque_size(queue()) ? to : -1;
}
int ringnav_prev(void) {
    if (follow_queue() || !s.active) {
        int left = M(MCL_POS), result = stock_prev_trampoline();
        back_to = M(MCL_MODE) == 2 && M(MCL_POS) != left ? left + 1 : 0;
        back_at = M(MCL_POS);
        return result;
    }
    /* the playing entry deleted with none after it: back to the one before, which history ends on */
    unsigned gone = (unsigned)M(MCL_POS) >= s.n;
    if (s.hn < 2 - gone) return 1;
    s.resume_key = s.resume_pending = 0;
    s.hn -= !gone;
    unsigned at = s.history[s.hn - 1];
    close_preload();
    /* The track left is unplayed again, so Next comes back to it rather than skipping it. */
    if ((unsigned)M(MCL_POS) < s.n) s.seen[M(MCL_POS)] = 0;
    M(MCL_POS) = (int)at;
    for (unsigned i = 0; i < s.n; ++i)
        if (s.order[i].index == at) {
            s.cursor = i;
            break;
        }
    s.next_valid = 0;
    s.dirty = 1;
    return mclStartPlayer();
}
/* Would this manual Next/Prev start the playing file again? With more tracks stock moves on, Repeat
 * One too, so outside advanced play a one-track queue does, unless Folder Skip leaves it for a sibling
 * folder (never an M3U's) or List Play's Next stops it. Shuffle's Previous goes back to its last
 * position before any folder, here this track. ponytail: stock Shuffle's pool can also redraw the
 * playing track, and Folder Skip with no sibling wraps; neither can be told before the press. */
static int change_restarts(int next) {
    int at = M(MCL_POS);
    if (s.active) return next ? playback_successor(0) == at : s.hn >= 2 && (int)s.history[s.hn - 2] == at;
    if (!queue() || deque_size(queue()) != 1 || (next && list_end())) return 0;
    if (M(MCL_TYPE) != 1 || !*(volatile unsigned char *)MCL_JUMPFOLDER || mclGetPlayM3uFlag()) return 1;
    return !next && M(MCL_MODE) == 2 && !M(MCL_LASTPOS);
}
/* A local queue's only: a DLNA controller or an online queue (0xf1, 0xf2) keeps stock's way. */
static int local_class(int cls) { return cls == 1 || (cls & 0xf000) == 0xf000; }
static int place_held(void) { return local_class(M(MCL_TYPE)) && resume_held(); }
/* A press that would restart a track holding a place only asks; a second of the same key within
 * RESTART_ASK_MS goes ahead, and resume_poll forgets the place as for any move to the start. Another
 * key asks again and arms for itself, so Previous can't confirm what a stray Next asked. */
#define RESTART_ASK_MS 3000
static unsigned restart_at;
static int restart_key; /* change_music's next: 0 Previous, 1 Next */
static int restart_ask; /* set here, maybe off the UI thread; toasted by the poll */
int restart_armed(int key) {
    return restart_at && restart_key == key && (unsigned)time_now_ms() - restart_at <= RESTART_ASK_MS;
}
static int restart_refused(int key, int restarts) {
    if (!restarts) return restart_at = 0;
    unsigned now = (unsigned)time_now_ms();
    if (restart_armed(key)) {
        resume_restart();
        return restart_at = 0;
    }
    restart_at = now | !now; /* never 0, no press yet */
    restart_key = key;
    return restart_ask = 1;
}
/* Manual Next/Prev (player_change_music). List Play stops at a library queue's end, as above. At
 * queue index 0 or n-1 stock wraps or loads a sibling folder itself; in Repeat All without Folder
 * Skip every branch calls mclNextSong(0)/mclPrevSong, so advanced order applies. MCL_MODE is
 * written directly: mclSetPlayMode would end advanced play. */
static int change_music(int next) {
    headset_manual();
    /* Stock's skip past a file it can't play is automatic, and never asks. */
    if (!press_natural && restart_refused(next, place_held() && change_restarts(next)))
        return 0; /* the caller toasts -1 (no storage) and -5 */
    press_stamp();
    if (!s.active) {
        if (!next || !list_end()) return stock_change_trampoline(next);
        ringnav_next(0); /* stops */
        return 0;        /* the caller toasts -1 (no storage) and -5 */
    }
    volatile unsigned char *skip = (volatile unsigned char *)MCL_JUMPFOLDER;
    int mode = M(MCL_MODE);
    unsigned char was = *skip;
    M(MCL_MODE) = 3;
    *skip = 0;
    int result = stock_change_trampoline(next);
    M(MCL_MODE) = mode;
    *skip = was;
    return result;
}
/* on_wm_timer_dialog's skip past a file it can't play (jalr player_next_music, 0x4f4dd4, which tail-branches
 * here with ra restored) is the only Next nobody pressed; its Folder Skip load stamps nothing either. */
#define UNPLAYABLE_NEXT_RET 0x4f4ddc
int ringnav_change(int next) {
    press_natural = __builtin_return_address(0) == (void *)UNPLAYABLE_NEXT_RET;
    int result = change_music(next);
    press_natural = 0;
    return result;
}
/* A user's seek stamps: a scrub or chapter (player_seek_time; Resume Songs' jump goes past this hook) and
 * Previous restarting the track. A-B Repeat and a phone call mclSetSeek themselves. */
int ringnav_seek_time(int sec) {
    press_stamp();
    return stock_seek_time_trampoline(sec);
}
/* player_prev_music's first move, its only caller, on anything playing or paused: past 10 s stock seeks
 * to the track's start and returns 1, in any queue and mode, without reaching player_change_music. A
 * refusal returns 1 too, so it goes no further. Under 11 s stock returns 0 and Previous changes track. */
int ringnav_seek_start(void) {
    int restarts = player_playtime() > 10;
    if (restart_refused(0, restarts && place_held())) return 1;
    if (restarts) press_stamp();
    return stock_seek_start_trampoline();
}
/* Is q's entry at the file playing or paused, the same CUE track, on a track holding a place? */
int playback_keeps(void *q, int at) {
    unsigned pos = (unsigned)M(MCL_POS);
    if (mclGetPlayStatus() == 1 || !place_held() || !q || !queue() || (unsigned)at >= deque_size(q) ||
        pos >= deque_size(queue()))
        return 0;
    void *ra = deque_at(q, (unsigned)at), *rb = deque_at(queue(), pos);
    const char *a = P(ra, REC_PATH), *b = P(rb, REC_PATH);
    return a && b && !strcmp(a, b) && I(ra, REC_CUE_START) == I(rb, REC_CUE_START) &&
           I(ra, REC_CUE_END) == I(rb, REC_CUE_END);
}
/* Now Playing, as on_backplay_click opens it, class 0xff: a pick means play this, so a paused file goes
 * on. Its Play waits for the DAC by itself (dac_resume), so no stamp. */
void playback_np(void) {
    static const int context[4] = { 0, 0, 0xff, 2 };
    navigator_switch_to_with_context("playing_page", context, 0);
    if (mclGetPlayStatus() == 3) player_play_pause();
}
/* Picks of the playing file reach player_start from each of these; none reads its result. */
#define PAGE_PICK_RET 0x53e514  /* playing_page_init, which opens Now Playing itself */
#define ALBUM_PLAY_RET 0x4a97c0 /* the album page's play (0x4a96c0), which stays on its page */
#define ALBUMINFO_PLAY_RET 0x4ac008 /* a song list's Play All (localmusic_songs_play_all) */
/* The same row of the same list: nothing starts. SyncLink's (0x5264c4, 0x5266b4), off the UI thread, stays
 * silent, the track playing on. From another list stock loads it, and the poll goes back to the place. Not a
 * press: an app's .play (press_natural), Shuffle Albums' load, DLNA and online. */
int ringnav_player_start(void *q, int at, int cls, int mode) {
    void *from = __builtin_return_address(0);
    if (!press_natural && !s.grouping && local_class(cls) && playback_keeps(q, at)) {
        if (at != M(MCL_POS) || deque_size(q) != deque_size(queue()) || cls != M(MCL_TYPE))
            resume_return();
        else {
            if (from == (void *)ALBUM_PLAY_RET || from == (void *)ALBUMINFO_PLAY_RET)
                playback_np();
            else if (from == (void *)PAGE_PICK_RET && mclGetPlayStatus() == 3)
                player_play_pause();
            return 1;
        }
    }
    return stock_player_start_trampoline(q, at, cls, mode);
}
int playback_group_skip(int forward) {
    if (!s.active && !playback_set(2, s.folder)) return 0;
    unsigned at = (unsigned)M(MCL_POS), next = s.n;
    if (at >= s.n) return 0;
    unsigned group = s.groups[at];
    if (forward) {
        for (unsigned i = s.cursor + 1; i < s.n; ++i)
            if (!s.seen[s.order[i].index] && s.groups[s.order[i].index] != group) {
                next = s.order[i].index;
                break;
            }
        if (next == s.n && s.repeat >= 3)
            for (unsigned i = 0; i < s.n; ++i)
                if (s.groups[s.cycle[i].index] != group) {
                    next = s.cycle[i].index;
                    break;
                }
        if (next == s.n) return 0;
        for (unsigned i = 0; i < s.n; ++i)
            if (s.groups[i] == group) s.seen[i] = 1;
        s.npend = 0;
        commit(next);
    } else {
        unsigned h = s.hn;
        while (h && s.groups[s.history[h - 1]] == group) --h;
        if (!h) return 0;
        group = s.groups[s.history[h - 1]];
        for (unsigned i = 0; i < s.n; ++i)
            if (s.groups[s.order[i].index] == group) {
                next = s.order[i].index;
                s.cursor = i;
                break;
            }
        if (next == s.n) return 0;
        for (unsigned i = 0; i < s.n; ++i)
            if (s.groups[i] == group) s.seen[i] = 0;
        s.hn = h;
        history(next);
        s.seen[next] = 1;
        s.npend = s.resume_key = s.resume_pending = 0;
        s.next_valid = 0;
        s.dirty = 1;
    }
    close_preload();
    M(MCL_POS) = (int)next;
    press_stamp();
    mclStartPlayer();
    playback_save();
    return 1;
}
int ringnav_preload(void) {
    if (!s.active) return stock_preload_trampoline();
    if (follow_queue()) return 1;
    int next = playback_successor(1);
    if (next < 0) return 1;
    M(MCL_PREPOS) = next;
    return mcl_open_preload();
}
void playback_insert(unsigned at, unsigned n, int next) {
    if (!s.active) {
        s.dirty = 1; /* the plain snapshot, on the next UI pass */
        return;
    }
    close_preload();
    /* Remap occurrences before grouping is recomputed. */
    unsigned *seen = calloc(s.n + n + 1, sizeof *seen);
    if (!seen) {
        s.active = 0;
        release();
        return;
    }
    for (unsigned i = 0; i < s.n; ++i) seen[i >= at ? i + n : i] = s.seen[i];
    free(s.seen);
    s.seen = seen;
    s.n += n;
    for (unsigned i = 0; i < s.hn; ++i)
        if (s.history[i] >= at) s.history[i] += n;
    s.active = rebuild(1);
    for (unsigned i = 0; i < s.npend; ++i)
        if (s.pend[i] >= at) s.pend[i] += n;
    /* Play Next again before the last one finished: the new tracks go first, in order */
    if (next)
        for (unsigned i = n; i-- > 0;) pend_front(at + i);
    playback_save();
}
/* Reverses the queue's rows [from, to). deque_at points into the deque's own storage and a row owns
 * its strings (REC_SIZE), so swapping two rows' bytes moves them without stock's copy. */
static void reverse_rows(void *queue, unsigned from, unsigned to) {
    char t[REC_SIZE];
    while (from + 1 < to) {
        char *a = deque_at(queue, from++), *b = deque_at(queue, --to);
        memcpy(t, a, REC_SIZE);
        memcpy(a, b, REC_SIZE);
        memcpy(b, t, REC_SIZE);
    }
}
/* Play Next or Add: the tracks land at pos+1 (next) or the end, and every queue index moves with
 * them. The stock side (deque, shuffle pool, last pick, previous index, gapless preload) is kept
 * whether or not advanced play is on; the preload closes as mclSetPlayMode @0x5bd98c does, flag -1,
 * then advanced play's own state follows. Returns the index the first track lands at. */
unsigned playback_queue_insert(void *queue, void *add, int next) {
    unsigned size = deque_size(queue), n = deque_size(add), pos = (unsigned)M(MCL_POS);
    unsigned at = next && pos < size ? pos + 1 : size;
    for (unsigned i = 0; i < n; ++i) _deque_push_back(queue, deque_at(add, i));
    /* Play Next turns the new rows, the only copies, from the end to at in place: rebuilding the
     * deque held a library-wide queue twice, and two copies of 47,283 songs got demo OOM-killed. */
    if (at < size) {
        reverse_rows(queue, at, size);
        reverse_rows(queue, size, size + n);
        reverse_rows(queue, at, size + n);
    }
    void *pool = P(MCL_POOL, 0);
    for (unsigned i = 0; i < deque_size(pool); ++i) {
        int *index = deque_at(pool, i);
        if ((unsigned)*index >= at) *index += (int)n;
    }
    for (unsigned i = 0; i < n; ++i) _deque_push_back(pool, (int)(at + i));
    if (M(MCL_LASTPOS) != -1 && (unsigned)M(MCL_LASTPOS) >= at) M(MCL_LASTPOS) += (int)n;
    /* back_to is the previous index + 1 (0 none), shifted like the rest */
    if (back_to && (unsigned)back_to - 1 >= at) back_to += (int)n;
    /* A preload always targets pos+1 (or the wrap); only an insert there makes it stale. */
    if (M(MCL_PRELOAD) == 1 && at == pos + 1) {
        if (M(MCL_FD) != -1) send(M(MCL_FD), "{mcl-closegapless\\null}", 23, 0);
        M(MCL_PRELOAD) = -1;
    }
    playback_insert(at, n, next);
    return at;
}
/* Up Next's hold: the queue's track at from plays next, ahead of any pending Play Next. Only the
 * pending list changes: from joins its front (out of its old place there), so the queue, its
 * indices, the order and every album or folder group stay as they were. commit then takes its
 * order entry to the cursor, as a Shuffle Songs pick does. Returns from, -1 when nothing moves.
 * ponytail: a track taken from the next pass's rows (Repeat All) still plays in that pass too. */
int playback_move(unsigned from) {
    void *q = queue();
    unsigned pos = (unsigned)M(MCL_POS);
    if (!s.active || !q || deque_size(q) != s.n || pos >= s.n || from >= s.n || from == pos)
        return -1;
    close_preload();
    s.next_valid = 0;
    s.dirty = 1;
    pend_front(from);
    playback_save();
    return (int)from;
}

/* Snapshot fields are little-endian uint32 on this MIPS target, no pointers. Version 3: after the
 * history comes the pending Play Next list, npend indices. Version 2 had one run of it, forced and
 * force_end in the header, and restores as the list. Version 4 is version 3's layout for a queue
 * without advanced play (plain): queue order, no history or list, restored without advanced play. */
typedef struct {
    unsigned magic, version, size, checksum, n, pos, elapsed, shuffle, repeat, folder, cursor, hn,
        npend, type;
} snapshot;
/* Elapsed time for the snapshot whose checksum is snapshot, at pos. */
typedef struct {
    unsigned checksum, snapshot, pos, elapsed;
} checkpoint;
static int save_elapsed(int sec) {
    checkpoint c = { 0, s.saved_sum, s.saved_pos, (unsigned)sec };
    c.checksum = hash_bytes(FNV_SEED, (const unsigned char *)&c.snapshot, 3 * 4);
    return BLOB_IO(CHECKPOINT_FILE, c, 1);
}
void playback_save(void) {
    /* Without advanced play a local queue (folder or library, as Play Next grows) is kept plain,
     * while Memory Playback or car mode would restore it (ringnav_memory reads nothing otherwise). */
    int plain = !s.active;
    unsigned type = (unsigned)M(MCL_TYPE), pos = (unsigned)M(MCL_POS);
    if (plain && ((!g_memory_play && !g_carmode) || !queue() ||
                  (type != 1 && (type & 0xf000) != 0xf000) || airplayGetFlag() == 2)) {
        s.dirty = 0;
        return;
    }
    unsigned n = plain ? deque_size(queue()) : s.n;
    if (plain && !n) return; /* boot's empty queue, before the snapshot restores */
    if (!n || n > SAVE_LIMIT) {
        unlink(QUEUE_FILE);
        s.dirty = 0;
        s.saved_queue = 0;
        return;
    }
    if (pos >= n) return; /* no track: the snapshot keeps the last one */
    /* ponytail: a plain queue is hashed whole each save (every 30 s playing, at most 999 tracks) to
     * see stock's own edits (View Queue's delete); hook those to drop the hash. */
    unsigned qsum = plain ? queue_hash(queue()) : 0;
    if (qsum != s.saved_queue) s.dirty = 1;
    unsigned hn = plain ? 0 : s.hn, npend = plain ? 0 : s.npend;
    int sec = 0, total = 0;
    mclGetPlayTime(&sec, &total);
    if (!sec && s.resume_wait && s.resume_key && (unsigned)M(MCL_POS) == s.resume_pos)
        sec = s.resume_elapsed;
    else if (sec > 0)
        s.resume_wait = 0;
    if (sec < 0) sec = 0;
    if (!s.dirty && !s.save_failed && sec == s.saved_elapsed && pos == s.saved_pos) return;
    /* Failed checkpoints stay pending; the UI loop retries at most every five seconds. */
    s.save_failed = 1;
    s.stamp = time_now_ms();
    if (!s.dirty && pos == s.saved_pos) {
        if (save_elapsed(sec)) s.saved_elapsed = sec, s.save_failed = 0;
        return;
    }
    s.dirty = 1;
    unsigned size = sizeof(snapshot) + (n * 3 + hn + npend) * 4;
    for (unsigned i = 0; i < n; ++i) {
        const char *path = P(deque_at(queue(), i), REC_PATH);
        unsigned len = path ? strlen(path) : 0;
        if (!len || len >= 1024) return;
        size += 12 + len + 1;
    }
    unsigned char *buf = calloc(size, 1);
    if (!buf) return;
    snapshot h = { 0x5132524e,
                   plain ? 4 : 3,
                   size,
                   0,
                   n,
                   pos,
                   (unsigned)sec,
                   plain ? 0 : (unsigned)s.shuffle,
                   plain ? 2 : (unsigned)s.repeat,
                   plain ? 0 : (unsigned)s.folder,
                   plain ? pos : s.cursor,
                   hn,
                   npend,
                   type };
    memcpy(buf, &h, sizeof h);
    unsigned char *p = buf + sizeof h;
    for (unsigned i = 0; i < n * 3 + hn + npend; ++i) {
        /* plain: the queue's order twice, nothing seen */
        unsigned index = plain && i < n * 2 ? i % n
                         : plain            ? 0
                         : i < n            ? s.order[i].index
                         : i < n * 2        ? s.cycle[i - n].index
                         : i < n * 3        ? s.seen[i - n * 2]
                         : i < n * 3 + hn   ? s.history[i - n * 3]
                                            : s.pend[i - n * 3 - hn];
        memcpy(p, &index, 4);
        p += 4;
    }
    for (unsigned i = 0; i < n; ++i) {
        void *r = deque_at(queue(), i);
        const char *path = P(r, REC_PATH);
        unsigned identity[] = { strlen(path) + 1, (unsigned)I(r, REC_CUE_START),
                                (unsigned)I(r, REC_CUE_END) };
        memcpy(p, identity, sizeof identity);
        p += sizeof identity;
        memcpy(p, path, identity[0]);
        p += identity[0];
    }
    unsigned sum = ((snapshot *)buf)->checksum = hash_bytes(FNV_SEED, buf, size);
    int ok = blob_io(QUEUE_FILE, QUEUE_FILE ".tmp", buf, size, 1);
    free(buf);
    if (ok) {
        s.saved_elapsed = sec;
        s.saved_pos = pos;
        s.saved_sum = sum;
        s.saved_queue = qsum;
        s.dirty = 0;
        s.save_failed = !save_elapsed(sec);
    }
}
static int library(void *unused) {
    /* Snapshot order wins; avoid stock's expensive pinyin title sort. */
    return getAllMusic(3);
}
typedef struct {
    void *record;
    unsigned ordinal;
} resume_row;
static int resume_cmp(void *r, const char *path, unsigned start, unsigned end) {
    int cmp = strcmp(P(r, REC_PATH), path);
    if (cmp) return cmp;
    unsigned a = (unsigned)I(r, REC_CUE_START), b = (unsigned)I(r, REC_CUE_END);
    return a != start ? (a > start ? 1 : -1) : b != end ? (b > end ? 1 : -1) : 0;
}
static int resume_before(const void *a, const void *b) {
    const resume_row *x = a, *y = b;
    void *r = y->record;
    int cmp = resume_cmp(x->record, P(r, REC_PATH), I(r, REC_CUE_START), I(r, REC_CUE_END));
    return cmp ? cmp : (x->ordinal > y->ordinal) - (x->ordinal < y->ordinal);
}
int directory(void *path) { return toolsLoadDirectory(path); }
/* Stock resume rebuilds an album queue (0xff10) from every album of its name (getMusicByAlbum):
 * the playing track's album alone. */
static int stock_memory(void *out) {
    int at = stock_memory_trampoline(out);
    return at >= 0 && I(g_memory_info, 0) == 0xff10 ? album_only(out, deque_at(out, (unsigned)at), at) : at;
}
int ringnav_memory(void *out) {
    if (!g_memory_play && !g_carmode) return stock_memory(out);
    void *f = fopen(QUEUE_FILE, "rb");
    if (!f) return stock_memory(out);
    snapshot h = { 0 };
    /* Raw header words, as the file has them: version 3 and 4 end with npend, type; version 2 with
     * forced, force_end, type, its one run, which the restore turns into the list. */
    unsigned head[15] = { 0 }, header_size, run = 0, run_end = 0;
    int ok = fread(head, 48, 1, f) == 1 && head[0] == 0x5132524e && head[1] >= 2 && head[1] <= 4;
    header_size = head[1] == 2 ? 60 : 56;
    ok = ok && fread(head + 12, header_size - 48, 1, f) == 1;
    memcpy(&h, head, 48);
    if (h.version == 2) {
        run = head[12], run_end = head[13];
        h.type = head[14];
    } else {
        h.npend = head[12];
        h.type = head[13];
    }
    /* No more songs than playback_save writes: a damaged header asks for no more memory than a
     * real snapshot takes (about 1 MB). */
    ok = ok && (h.type == 1 || (h.type & 0xf000) == 0xf000) && h.type <= 0xffff && h.n &&
         h.n <= SAVE_LIMIT && h.pos < h.n && h.cursor < h.n && h.hn <= HISTORY_LIMIT &&
         (h.version == 2 ? run <= h.n && run_end <= h.n && (!run || run <= run_end)
                         : h.npend <= PEND_LIMIT) &&
         h.shuffle < 5 && h.repeat < 6 && h.folder < 2 && h.elapsed <= 0x7fffffff &&
         h.size >= header_size + (h.n * 3 + h.hn + h.npend) * 4 + h.n * 14 &&
         h.size <= header_size + (h.n * 3 + h.hn + h.npend) * 4 + h.n * 1036;
    unsigned char *buf = ok ? calloc(h.size, 1) : 0;
    if (buf) {
        memcpy(buf, head, header_size);
        ok = fread(buf + header_size, h.size - header_size, 1, f) == 1;
        unsigned char extra;
        if (fread(&extra, 1, 1, f)) ok = 0;
        ((snapshot *)buf)->checksum = 0;
        if (hash_bytes(FNV_SEED, buf, h.size) != h.checksum) ok = 0;
    } else
        ok = 0;
    if (fclose(f)) ok = 0;
    checkpoint c;
    if (ok && BLOB_IO(CHECKPOINT_FILE, c, 0) && c.snapshot == h.checksum && c.pos == h.pos &&
        c.elapsed <= 0x7fffffff && hash_bytes(FNV_SEED, (const unsigned char *)&c.snapshot, 3 * 4) == c.checksum)
        h.elapsed = c.elapsed;
    /* Validate the entire snapshot before queries or mutations. */
    unsigned *indices = (unsigned *)(buf ? buf + header_size : 0);
    unsigned char *p = buf ? buf + header_size + (h.n * 3 + h.hn + h.npend) * 4 : 0;
    unsigned *map = ok ? calloc(h.n, sizeof *map) : 0;
    if (!map) ok = 0;
    for (unsigned i = 0; ok && i < h.n * 3 + h.hn + h.npend; ++i)
        if (i >= h.n * 2 && i < h.n * 3 ? indices[i] > 1 : indices[i] >= h.n) ok = 0;
    for (unsigned cycle = 0; ok && cycle < 2; ++cycle) {
        memset(map, 0, h.n * 4);
        for (unsigned i = 0; i < h.n; ++i)
            if (map[indices[cycle * h.n + i]]++) ok = 0;
    }
    if (ok && indices[h.cursor] != h.pos) ok = 0;
    for (unsigned i = 0; ok && i < h.n; ++i) {
        unsigned identity[3];
        if ((unsigned)(buf + h.size - p) < 12) {
            ok = 0;
            break;
        }
        memcpy(identity, p, 12);
        p += 12;
        unsigned len = identity[0];
        if (len < 2 || len > 1024 || len > (unsigned)(buf + h.size - p) || p[len - 1] ||
            strlen((const char *)p) != len - 1 || identity[1] > 0x7fffffff ||
            identity[2] > 0x7fffffff) {
            ok = 0;
            break;
        }
        p += len;
    }
    if (ok && p != buf + h.size) ok = 0;
    void *rows = 0, *q = 0, *all = 0;
    if (ok) {
        q = _create_deque("stSongInfo");
        deque_init(q);
        int count;
        all = staged(library, 0, &count);
        unsigned n = 0;
        resume_row *lookup = calloc(deque_size(all), sizeof *lookup);
        if (!lookup && deque_size(all)) ok = 0;
        for (unsigned i = 0; ok && i < deque_size(all); ++i) {
            void *r = deque_at(all, i);
            if (I(r, REC_TYPE) == 8 && P(r, REC_PATH)) lookup[n++] = (resume_row){ r, i };
        }
        if (n) qsort(lookup, n, sizeof *lookup, resume_before);
        char folder[1024] = "";
        p = buf + header_size + (h.n * 3 + h.hn + h.npend) * 4;
        for (unsigned i = 0; ok && i < h.n; ++i) {
            unsigned id[3];
            memcpy(id, p, 12);
            p += 12;
            const char *path = (const char *)p;
            p += id[0];
            map[i] = ~0u;
            if (access(path, 0)) continue;
            unsigned lo = 0, hi = n;
            while (lo < hi) {
                unsigned mid = lo + (hi - lo) / 2;
                if (resume_cmp(lookup[mid].record, path, id[1], id[2]) < 0)
                    lo = mid + 1;
                else
                    hi = mid;
            }
            if (lo < n && !resume_cmp(lookup[lo].record, path, id[1], id[2])) {
                map[i] = deque_size(q);
                _deque_push_back(q, lookup[lo].record);
            }
            if (map[i] != ~0u) continue;
            char parent[1024];
            memcpy(parent, path, id[0]);
            char *slash = strrchr(parent, '/');
            if (!slash) continue;
            *slash = 0;
            if (strcmp(folder, parent) || !rows) {
                if (rows) deque_destroy(rows);
                int count;
                rows = staged(directory, parent, &count);
                memcpy(folder, parent, strlen(parent) + 1);
            }
            for (unsigned j = 0; j < deque_size(rows); ++j) {
                void *r = deque_at(rows, j);
                if (I(r, REC_TYPE) == 8 && !strcmp(P(r, REC_PATH), path) &&
                    (unsigned)I(r, REC_CUE_START) == id[1] &&
                    (unsigned)I(r, REC_CUE_END) == id[2]) {
                    map[i] = deque_size(q);
                    _deque_push_back(q, r);
                    break;
                }
            }
        }
        if (rows) deque_destroy(rows);
        free(lookup);
        deque_destroy(all);
        ok = deque_size(q) != 0;
    }
    int result = -1;
    if (ok) {
        unsigned pos = map[h.pos];
        if (pos == ~0u) {
            for (unsigned i = h.cursor; i < h.n && pos == ~0u; ++i) pos = map[indices[i]];
            if (pos == ~0u) pos = 0;
            h.elapsed = 0;
        }
        s.restoring = 1;
        mclLoadPlayList(q, (int)pos, (int)h.type);
        s.shuffle = (int)h.shuffle;
        s.repeat = (int)h.repeat;
        s.folder = (int)h.folder;
        s.active = h.version == 4 ? 0 : rebuild(0);
        if (s.active) {
            unsigned k = 0;
            for (unsigned i = 0; i < h.n; ++i)
                if (map[indices[i]] != ~0u) s.order[k++].index = map[indices[i]];
            k = 0;
            for (unsigned i = 0; i < h.n; ++i)
                if (map[indices[h.n + i]] != ~0u) s.cycle[k++].index = map[indices[h.n + i]];
            s.hn = 0;
            for (unsigned i = 0; i < h.hn; ++i)
                if (map[indices[h.n * 3 + i]] != ~0u) history(map[indices[h.n * 3 + i]]);
            if (!s.hn || s.history[s.hn - 1] != pos) history(pos);
            memset(s.seen, 0, s.n * 4);
            for (unsigned i = 0; i < h.n; ++i)
                if (map[i] != ~0u) s.seen[map[i]] = indices[h.n * 2 + i];
            s.seen[pos] = 1;
            for (unsigned i = 0; i < s.n; ++i)
                if (s.order[i].index == pos) {
                    s.cursor = i;
                    break;
                }
            s.npend = 0;
            if (h.version == 2) {
                /* the run [forced-1, force_end), in order */
                for (unsigned i = run_end; run && i-- > run - 1;)
                    if (map[i] != ~0u) pend_front(map[i]);
            } else
                for (unsigned i = h.npend; i-- > 0;) {
                    unsigned at = map[indices[h.n * 3 + h.hn + i]];
                    if (at != ~0u) pend_front(at);
                }
        }
        if (s.active || h.version == 4) {
            deque_assign(out, q);
            I(g_memory_info, 0) = (int)h.type;
            mclSetStartSeekTime(g_memory_play == 2 || g_carmode ? (int)h.elapsed : 0);
            s.resume_key = fnv(FNV_SEED, P(deque_at(queue(), pos), REC_PATH));
            s.resume_pos = pos;
            s.resume_pending = s.resume_wait = 1;
            s.resume_elapsed = g_memory_play == 2 || g_carmode ? (int)h.elapsed : 0;
            result = (int)pos;
        }
        s.restoring = result >= 0 ? 2 : 0;
    }
    if (q) deque_destroy(q);
    free(map);
    free(buf);
    return result >= 0 ? result : stock_memory(out);
}
/* The queue snapshot's startup position takes precedence over per-track long-song resume; one with
 * no time to give (Memory Playback: Track) leaves the track's own place in charge. */
int playback_resumed(void *r) {
    int pending = s.resume_pending;
    s.resume_pending = 0;
    return pending && s.resume_elapsed && (unsigned)M(MCL_POS) == s.resume_pos &&
           fnv(FNV_SEED, P(r, REC_PATH)) == s.resume_key;
}
/* Stock Shuffle's next, drawn ahead so Crossfade can ask about the file. Stock's pick (0x5ba7d0) takes
 * the current index out of the pool and sets the position to a random one left there, which stays
 * until it is current: so ringnav_shuffle overrides the position with this draw, a uniform one of the
 * same set. Neither touches the pool. The two records say the queue under it hasn't changed.
 * ponytail: with under 2 in the pool the pick refills it (or Folder Skip jumps): -2, unknown. */
static struct { unsigned at, index; void *now, *next; } peek __attribute__((section(".scratch")));
static int peek_holds(unsigned at) {
    void *q = queue();
    unsigned n = q ? deque_size(q) : 0;
    return peek.now && peek.at == at && at < n && peek.index < n && deque_at(q, at) == peek.now &&
           deque_at(q, peek.index) == peek.next;
}
static int shuffle_peek(unsigned at) {
    if (peek_holds(at)) return (int)peek.index;
    void *pool = P(MCL_POOL, 0);
    unsigned k = pool ? deque_size(pool) : 0, n = deque_size(queue());
    peek.now = 0;
    for (int tries = 0; k >= 2 && tries < 8; ++tries) {
        unsigned x = *(unsigned *)deque_at(pool, rand_below(k));
        if (x != at && x < n) {
            peek.at = at, peek.index = x;
            peek.now = deque_at(queue(), at), peek.next = deque_at(queue(), x);
            return (int)x;
        }
    }
    return -2;
}
int playback_peek(unsigned at) {
    int to = peek_holds(at) ? (int)peek.index : -1;
    peek.now = 0;
    return to;
}
/* The track that plays after this one, or -1: Crossfade's next (crossfade.c). Stock preloads pos+1
 * only in order and repeat-all (mclSetPlayMode's modes 0 and 3), repeat-all's wrapping to 0; stock
 * Shuffle (2) goes back after a Previous, to a Play next, or to the draw above. */
static int playback_next(void) {
    if (s.active) return playback_successor(1);
    unsigned at = (unsigned)M(MCL_POS);
    int mode = M(MCL_MODE);
    unsigned n = queue() ? deque_size(queue()) : 0;
    if (mode == 2) {
        if (at >= n || n < 2) return -1;
        int to = shuffle_forced();
        if (to >= 0) return to;
        if (back_to && (int)at == back_at && (unsigned)back_to <= n) return back_to - 1;
        return shuffle_peek(at);
    }
    if (mode == 3 && n && at + 1 == n) return 0;
    return (mode == 0 || mode == 3) && at + 1 < n ? (int)at + 1 : -1;
}
void playback_poll(void) {
    ui_thread = pthread_self();
    wheel_pct(); /* the saved sensitivity from the first UI pass */
    if (too_long) {
        too_long = 0;
        toast("Too long to play on the Q2: split it into parts");
    }
    if (restart_ask) {
        restart_ask = 0;
        toast("Press again to restart");
    }
    follow_queue(); /* before Crossfade reads the next track */
    xfade_poll(playback_next(), !s.active && M(MCL_MODE) == 2);
    mono_sync();
    unsigned now = time_now_ms();
    /* playback_save writes only what changed, at most every 5 s. A track that just plays on
     * writes its elapsed second every 30 s instead (a synced flash write each); a new track, a
     * pause or a seek go within 5 s, a failed write retries every 30 s, and power-off saves
     * exactly (ringnav_savequeue). */
    if (!(s.dirty && !s.save_failed)) {
        if (now - s.stamp < (s.save_failed ? 30000u : 5000u) || now - s.poll_stamp < 5000) return;
        s.poll_stamp = now;
        int sec = 0, total = 0;
        mclGetPlayTime(&sec, &total);
        if (now - s.stamp < 30000 && !s.save_failed && mclGetPlayStatus() == 2 &&
            (unsigned)M(MCL_POS) == s.saved_pos && (unsigned)(sec - s.saved_elapsed) <= 35)
            return;
    }
    playback_save(), s.stamp = now;
}

int ringnav_savequeue(void) {
    ringnav_clean_end(); /* stock's only call is into_poweroff's */
    playback_save();
    return stock_savequeue_trampoline();
}

/* into_poweroff is stock's only save_memoryplay_info call, so a reboot (device_reboot: Restart, a
 * Bluetooth recovery) or a firmware update came back to the queue and second of the last power-off.
 * Both save first. The update's dialog (dialog_checkfw_dialog_init, the TF card update's and
 * Network Update's) stops the player before its thread flashes and reboots, so it saves on open.
 * Stopped, nothing is saved: stock's save skips it and the snapshot keeps its second. */
static void reboot_save(void) {
    ringnav_clean_end();
    if (mclGetPlayStatus() != 1) save_memoryplay_info(); /* ringnav_savequeue */
}
int ringnav_reboot(void) {
    reboot_record(__builtin_return_address(0)); /* a Bluetooth abnormal reboot's crash log */
    reboot_save();
    return stock_reboot_trampoline();
}
int ringnav_checkfw(void *win, void *ctx) {
    reboot_save();
    return stock_checkfw_trampoline(win, ctx);
}

/* Stock passes its audio extension list and the final dot suffix in folder and scan paths.
 * Keep every existing format; TagLib's FileRef already dispatches M4B to MP4. Folder view's
 * listing (toolsLoadDirectory) in the card's Videos folder also takes videos, which a row click
 * hands to books_video; the library scan never does, and no queue keeps them (no_videos). */
#define LOAD_DIRECTORY_RET 0x5d4cb4 /* toolsLoadDirectory's (build.py AUDIO_EXTENSION_CALLS) */
extern int folder_videos(void);
char *ringnav_audio_extension(const char *formats, const char *ext) {
    if (!strcasecmp(ext, ".m4b") || (__builtin_return_address(0) == (void *)LOAD_DIRECTORY_RET &&
                                     video_ext(ext) && folder_videos()))
        return (char *)ext;
    return strstr(formats, ext);
}
