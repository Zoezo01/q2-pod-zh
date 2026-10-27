/* Card apps: programs that run from the card beside Q2 Pod, installed by System
 * settings' Apps from their own GitHub releases (netupdate.c) or unzipped to the card by hand, as
 * Spotify's librespot is. Q2 Pod knows none of them by name: each is a card folder .<id> holding
 *   q2app    its manifest, key=value lines: name (its Streaming row's), search (the search box's
 *            hint) and qr (1: its row turns it on and shows its QR code once it answers);
 *   run      its launcher, which copies the app to /tmp and runs it there in a restart loop, so
 *            the card is never kept busy, leaving the loop's pid in /tmp/q2-<id>;
 *   q2-<id>  the app;
 *   icon.png, icon-40.png
 *            its Streaming row's icon, 52 px (Legacy's rows) and 40 px (iPod's, SET_ICON), each
 *            drawn 1:1; without them, the shared stream_app.
 * Streaming lists those on the card (navigation.c ringnav_stream). A row starts its app and opens
 * addon_page, a list the app draws over Q2SPOT/1 (docs/librespot.md) on /tmp/q2-<id>.browse.sock:
 * each row's kind says what a press does (a local row plays a song or folder from the card,
 * navigation.c's songs_open). Its status file /tmp/q2-<id>.state says whether to hold standby off
 * (hold=1: files moving, a phone using its page) and carries its web page's QR code, address and
 * PIN, which qr_open's page draws. A TERM to the loop's pid asks it to end. An app that plays as well
 * (q2-deezer's Stream mode) names a card folder of songs in /tmp/q2-<id>.play, which stream_poll
 * plays as it fills. */
#include "offsets.inc"
#include "peq.h"
#include "playback.h"
#include "stock.h"

#define ADDON_POLL_MS 500
#define ADDON_START_MS 10000 /* no socket this long after the start: it isn't running */
#define ADDON_LIVE_MS 2000   /* a level whose reply says "refresh" is asked again this often */
#define ADDON_WAIT_MS 15000  /* a request's deadline, as Spotify's */
#define ADDON_DEPTH 6
#define ADDON_ROWS 50
#define REPLY_MAX (64 << 10) /* Q2SPOT/1's limits */
#define REQ_MAX (8 << 10)
#define AWAY_MS 3000 /* USB storage waits at most this long for an app to let the card go */
#define SLOW_MS 3000 /* a page loading or starting this long offers to stop the app */
#define RESTART_MS 20000 /* Retry's restart starts the app by then, its old loop gone or not */
#define APP_ID 16  /* an id's bytes, its NUL included */
#define QR_PX 186  /* the code's white plate: 5 px modules up to a 33-module code */
#define QR_COL 214 /* the text beside it */
#define CARD "/mnt/mmc"

/* An app found on the card, kept for the life of demo in its slot. present: on the card at the
 * last scan; relaunch: stopped for USB storage, to start again; restart: stopped by Retry (its
 * socket gone), to start again once its loop is gone. */
typedef struct {
    char id[APP_ID], name[32], tips[32], bin[48], run[48], pid[32], state[40], browse[48], icon[56];
    int qr, present, launched, hold, relaunch, restart;
    unsigned polled, launch_at, restart_at;
} app_t;

static struct {
    app_t a[APPS_MAX];
    int n, shown, order[APPS_MAX]; /* order: the slots on the card, by name */
} ap __attribute__((section(".scratch")));

/* An id goes into shell commands: lowercase letters, digits and '-' only. */
static int valid_id(const char *s) {
    unsigned k = 0;
    for (; s[k]; ++k)
        if (k + 1 >= APP_ID ||
            !((s[k] >= 'a' && s[k] <= 'z') || (s[k] >= '0' && s[k] <= '9') || s[k] == '-'))
            return 0;
    return k > 0;
}

static app_t *slot(const char *id) {
    for (int i = 0; i < ap.n; ++i)
        if (!strcmp(ap.a[i].id, id)) return &ap.a[i];
    if (ap.n == APPS_MAX) return 0;
    app_t *a = &ap.a[ap.n++];
    tk_snprintf(a->id, sizeof a->id, "%s", id);
    tk_snprintf(a->bin, sizeof a->bin, CARD "/.%s/q2-%s", id, id);
    tk_snprintf(a->run, sizeof a->run, "/bin/sh " CARD "/.%s/run", id);
    tk_snprintf(a->pid, sizeof a->pid, "/tmp/q2-%s", id);
    tk_snprintf(a->state, sizeof a->state, "/tmp/q2-%s.state", id);
    tk_snprintf(a->browse, sizeof a->browse, "/tmp/q2-%s.browse.sock", id);
    return a;
}

/* Streaming's page (navigation.c): the card's apps found again, their names read; how many there
 * are, by name for addon_name. A slot keeps its app (and its running state) from scan to scan. */
int addon_scan(void) {
    char raw[512], path[64];
    void *d = opendir(CARD);
    for (int i = 0; i < ap.n; ++i) ap.a[i].present = 0;
    for (struct dirent *e; d && (e = readdir(d));) {
        app_t *a;
        if (e->d_name[0] != '.' || !valid_id(e->d_name + 1)) continue;
        tk_snprintf(path, sizeof path, CARD "/%s/q2app", e->d_name);
        if (!state_read(path, raw, sizeof raw) || !(a = slot(e->d_name + 1))) continue;
        field(raw, "name", a->name, sizeof a->name);
        if (!a->name[0]) tk_snprintf(a->name, sizeof a->name, "%s", a->id);
        field(raw, "search", a->tips, sizeof a->tips);
        if (!a->tips[0]) tk_snprintf(a->tips, sizeof a->tips, "搜索");
        a->qr = number(raw, "qr") == 1;
        a->present = !access(a->bin, 0);
        /* the edition's size first, the other scaled to fit (navigation.c set_child), else
         * stream_app */
        static const char *const icons[2] = { IPOD ? "icon-40.png" : "icon.png",
                                              IPOD ? "icon.png" : "icon-40.png" };
        tk_snprintf(a->icon, sizeof a->icon, "stream_app");
        for (int i = 1; i >= 0; --i) {
            tk_snprintf(path, sizeof path, CARD "/%s/%s", e->d_name, icons[i]);
            if (!access(path, 0)) tk_snprintf(a->icon, sizeof a->icon, "file://%s", path);
        }
    }
    if (d) closedir(d);
    ap.shown = 0;
    for (int i = 0; i < ap.n; ++i) { /* by name: an insertion, eight at most */
        if (!ap.a[i].present) continue;
        int k = ap.shown++;
        for (; k && strcasecmp(ap.a[ap.order[k - 1]].name, ap.a[i].name) > 0; --k)
            ap.order[k] = ap.order[k - 1];
        ap.order[k] = i;
    }
    return ap.shown;
}

const char *addon_name(int k) { return k >= 0 && k < ap.shown ? ap.a[ap.order[k]].name : 0; }
const char *addon_id(int k) { return k >= 0 && k < ap.shown ? ap.a[ap.order[k]].id : 0; }
const char *addon_icon(int k) { return k >= 0 && k < ap.shown ? ap.a[ap.order[k]].icon : 0; }

/* Whether an image's name is a card app's Streaming icon, file:///mnt/mmc/.<id>/icon*.png
 * (navigation.c ringnav_image_add: Minimal greys it as Streaming's own icons). */
int addon_is_icon(const char *name) {
    static const char *const ends[] = { "/icon.png", "/icon-40.png" };
    unsigned n = name ? strlen(name) : 0;
    if (!n || !tk_str_start_with(name, "file://" CARD "/.")) return 0;
    for (unsigned i = 0; i < 2; ++i) {
        unsigned k = strlen(ends[i]);
        if (n > k && !strcmp(name + n - k, ends[i])) return 1;
    }
    return 0;
}

/* A pid file whose loop is gone (crashed, or killed) is removed, so no wait spends AWAY_MS on it.
 * ponytail: trusts the pid in the file; a loop that died without removing it, its pid since reused,
 * would TERM that process. A flock in run would close this. */
static void stop_cmd(const char *pid, char *cmd, unsigned n) {
    snprintf(cmd, n, "kill $(cat %s) 2>/dev/null || rm -f %s", pid, pid);
}

static void launch(app_t *a) {
    const char *busy = shelf_busy();
    if (busy && !strcmp(busy + 1, a->id)) return; /* the Apps page is replacing its files */
    a->launched = 1;
    a->launch_at = time_now_ms();
    system(a->run); /* exits at once when the app runs already */
}

/* Stream mode (q2-deezer's stream.go): an app's /tmp/q2-<id>.play holds "<seq> <folder>", a hidden
 * card folder it fills with a list's songs, named in play order ("01 - Title [id].flac"), as they
 * download. A new one is played from its first song once one is there (the folder's songs in name
 * order, as a local row's album is: songs_open), Now Playing opening as for a press. While the queue
 * is still that folder's, songs that come later (named after the last one queued) join its end, as Add
 * to Queue adds them (playback_queue_insert); the path of the one playing is left in
 * /tmp/q2-<id>.playing, so the app fetches only a few ahead and deletes the ones long played. */
static struct {
    char play[192];    /* the .play file as read last */
    char dir[160];     /* its folder */
    char last[192];    /* the name of the last of its songs queued */
    char playing[256]; /* the .playing path written last */
    int state;         /* S_WAIT for its first song, S_PLAY its queue playing, S_DONE another queue */
    unsigned timer;    /* the first song's start, on a timer (a window opens) */
    long seen;         /* the folder's mtime at the last scan that came well after it */
} sm __attribute__((section(".scratch")));
enum { S_WAIT, S_PLAY, S_DONE };

/* An audio file in dir named after last ("": any), not a hidden one or a download's .part. */
static int stream_newer(const char *dir, const char *last) {
    /* ponytail: only once the folder changes (o32 stat, st_mtime word 16), not a scan every poll; a
     * scan in the second of a change is not trusted, as a file could land later in that second. */
    unsigned st[36];
    if (__xstat(3, dir, st)) return 0;
    if ((long)st[16] == sm.seen) return 0;
    if ((long)st[16] < time((void *)0) - 1) sm.seen = (long)st[16];
    void *d = opendir(dir);
    int found = 0;
    for (struct dirent *e; d && !found && (e = readdir(d));) {
        const char *n = e->d_name, *dot = strrchr(n, '.');
        found = n[0] != '.' && dot && (!strcasecmp(dot, ".flac") || !strcasecmp(dot, ".mp3")) &&
                strcmp(n, last) > 0;
    }
    if (d) closedir(d);
    return found;
}

static int stream_start(const void *unused) {
    (void)unused;
    sm.timer = 0;
    void *songs = app_album_songs(sm.dir);
    unsigned n = deque_size(songs);
    if (n && sm.state == S_WAIT) {
        tk_snprintf(sm.last, sizeof sm.last, "%s", (const char *)P(deque_at(songs, n - 1), REC_NAME));
        sm.state = S_PLAY;
        play_folder(songs, 0, 0xf001); /* playing_page copies the queue at once */
    }
    deque_destroy(songs);
    return 0;
}

/* Whether the queue playing is the session's: its first song in its folder. */
static void *stream_queue(void) {
    void *q = P(mcl_pdeqplaylist, 0);
    const char *p = q && deque_size(q) ? P(deque_at(q, 0), REC_PATH) : 0;
    return p && tk_str_start_with(p, sm.dir) && p[strlen(sm.dir)] == '/' ? q : 0;
}

static void stream_poll(app_t *a) {
    char path[48], raw[sizeof sm.play];
    tk_snprintf(path, sizeof path, "/tmp/q2-%s.play", a->id);
    if (!state_read(path, raw, sizeof raw)) return;
    if (strcmp(raw, sm.play)) { /* a new session */
        const char *dir = strstr(raw, " ");
        tk_snprintf(sm.play, sizeof sm.play, "%s", raw);
        tk_snprintf(sm.dir, sizeof sm.dir, "%s", dir ? dir + 1 : "");
        for (char *c = sm.dir; *c; ++c)
            if (*c == '\n' || *c == '\r') *c = 0;
        sm.last[0] = sm.playing[0] = 0;
        sm.seen = 0;
        /* a hidden folder of the card, as the app's own are */
        sm.state = tk_str_start_with(sm.dir, CARD "/.") && !strstr(sm.dir, "..") ? S_WAIT : S_DONE;
        stop_timer(&sm.timer);
    }
    if (sm.state == S_WAIT) {
        if (!sm.timer && stream_newer(sm.dir, "")) sm.timer = timer_add(stream_start, 0, 0);
        return;
    }
    void *q = sm.state == S_PLAY ? stream_queue() : 0;
    if (!q) {
        sm.state = S_DONE; /* something else plays now: the session is left */
        return;
    }
    if (stream_newer(sm.dir, sm.last)) {
        void *songs = app_album_songs(sm.dir), *add = _create_deque("stSongInfo");
        deque_init(add);
        for (unsigned i = 0, n = deque_size(songs); i < n; ++i) {
            void *r = deque_at(songs, i);
            if (strcmp(P(r, REC_NAME), sm.last) > 0) _deque_push_back(add, r);
        }
        if (deque_size(add)) {
            void *r = deque_at(add, deque_size(add) - 1);
            tk_snprintf(sm.last, sizeof sm.last, "%s", (const char *)P(r, REC_NAME));
            playback_queue_insert(q, add, 0);
        }
        deque_destroy(add);
        deque_destroy(songs);
    }
    unsigned pos = (unsigned)I(MCL_POS, 0);
    const char *now = pos < deque_size(q) ? P(deque_at(q, pos), REC_PATH) : 0;
    if (now && strcmp(now, sm.playing)) {
        char line[sizeof sm.playing + 1];
        int n = tk_snprintf(line, sizeof line, "%s\n", now);
        tk_snprintf(sm.playing, sizeof sm.playing, "%s", now);
        tk_snprintf(path, sizeof path, "/tmp/q2-%s.playing", a->id);
        blob_io(path, "/tmp/q2-stream.tmp", line, (unsigned)n, 1);
    }
}

/* ringnav_sleep, every UI loop pass, paced to ADDON_POLL_MS once its row started it: standby and
 * auto power-off held while the app asks (hold=1), as Spotify's poll does while it plays; the
 * screen still turns off. An app that stopped by itself (idle, or from the phone) is let go, and
 * one stopped for USB storage starts again once the card is back. */
static void poll_one(app_t *a) {
    unsigned now = time_now_ms();
    if ((!a->launched && !a->relaunch && !a->restart) || now - a->polled < ADDON_POLL_MS) return;
    a->polled = now;
    if (a->restart) { /* its loop gone (the launcher kills a frozen app 15 s after its TERM) */
        if (access(a->pid, 0) || now - a->restart_at > RESTART_MS) a->restart = 0, launch(a);
        return;
    }
    if (a->relaunch) {
        if (!navigator_window_is_exist("usbmode_page") && !access(a->bin, 0))
            a->relaunch = 0, launch(a);
        return;
    }
    char raw[64 + 16];
    state_read(a->state, raw, sizeof raw); /* addr= and hold= sort before the long qr= */
    a->hold = number(raw, "hold") == 1;
    if (a->hold) reset_poweroptions_timer(1, 1, 0); /* standby, auto power-off; not the screen's */
    stream_poll(a);
    if (access(a->pid, 0) && now - a->launch_at > ADDON_START_MS) a->launched = a->hold = 0;
}

void addon_poll(void) {
    for (int i = 0; i < ap.n; ++i) poll_one(&ap.a[i]);
}

/* An app asked to end through its loop, not its socket, so a frozen one stops too; restart: started
 * again once its loop is gone (poll_one). */
static void app_stop(app_t *a, int restart) {
    char cmd[96];
    stop_cmd(a->pid, cmd, sizeof cmd), system(cmd);
    a->launched = a->hold = a->relaunch = 0;
    a->restart = restart, a->restart_at = time_now_ms();
}

/* Streaming's Stop Streaming Services, and Home's Rockbox row: each asked to end (its downloads
 * or queue saved). */
void addon_stop(void) {
    char cmd[96];
    for (int i = 0; i < ap.n; ++i) {
        app_t *a = &ap.a[i];
        if (a->launched || !access(a->pid, 0)) stop_cmd(a->pid, cmd, sizeof cmd), system(cmd);
        a->launched = a->hold = a->relaunch = a->restart = 0;
    }
}

/* usbmode_page_init's hook (navigation.c), before stock unmounts the card for USB storage: every
 * app asked to end at once, then up to AWAY_MS in all for their loops to be gone (each removes its
 * pid file once the app has exited), so nothing of them holds the card; each starts again after
 * (addon_poll). On the UI thread; addon_quit is the Apps page's worker's. */
void addon_card_away(void) {
    char cmd[96];
    int left = 0;
    for (int i = 0; i < ap.n; ++i) {
        app_t *a = &ap.a[i];
        int running = a->launched || a->restart || !access(a->pid, 0);
        if (!access(a->pid, 0)) stop_cmd(a->pid, cmd, sizeof cmd), system(cmd), left = 1;
        a->launched = a->hold = a->restart = 0;
        a->relaunch = running;
    }
    for (unsigned start = time_now_ms(); left && time_now_ms() - start < AWAY_MS;) {
        sleep_ms(50);
        left = 0;
        for (int i = 0; i < ap.n; ++i) left |= !access(ap.a[i].pid, 0);
    }
}

/* The Apps page (netupdate.c) replaces or removes the files in dir (".<id>"): the app there, if
 * one, forgotten on the UI thread, so its row starts the new one (or none); then, on the Apps
 * page's worker, asked to end and waited for, so its files are closed before they move. */
void addon_forget(const char *dir) {
    for (int i = 0; dir && dir[0] == '.' && i < ap.n; ++i)
        if (!strcmp(ap.a[i].id, dir + 1))
            ap.a[i].launched = ap.a[i].hold = ap.a[i].relaunch = ap.a[i].restart = 0;
}
void addon_quit(const char *dir) { /* addon_card_away's wait, libc's clock and sleep, one app */
    char pid[32], cmd[96];
    if (!dir || dir[0] != '.' || !valid_id(dir + 1)) return;
    snprintf(pid, sizeof pid, "/tmp/q2-%s", dir + 1);
    if (access(pid, 0)) return;
    stop_cmd(pid, cmd, sizeof cmd);
    system(cmd);
    for (unsigned long long end = now_ms() + AWAY_MS; !access(pid, 0) && now_ms() < end;)
        usleep(50000);
}

/* A Q2SPOT/1 request in flight on its pthread, as Spotify's browser's (spotify.c): written from
 * buf, the reply then read into it, one connection a request. */
static struct {
    unsigned long thread;
    int running, fail; /* fail: REQ_* */
    volatile int cancel, done;
    unsigned id, n, len; /* id: the latest request's, never reused */
    char *buf;
    const char *path; /* the app's browse socket */
} req __attribute__((section(".scratch")));
enum { REQ_OK, REQ_NOSOCK, REQ_FAIL };

/* Written, shut for writing, and read to EOF within ADDON_WAIT_MS, 100 ms at a time so a cancel is
 * joined at once. */
static void *worker(void *unused) {
    struct unix_addr to = { 1, { 0 } };
    tk_snprintf(to.path, sizeof to.path, "%s", req.path);
    unsigned long long end = now_ms() + ADDON_WAIT_MS;
    unsigned sent = 0;
    int fd = socket(1, 2, 0), k = 1; /* AF_UNIX, SOCK_STREAM (MIPS numbering) */
    req.fail = REQ_NOSOCK;
    if (fd >= 0 && fd < 1024 && !fcntl(fd, 4, 0x80) &&
        !connect(fd, &to, sizeof to)) { /* F_SETFL, O_NONBLOCK (MIPS) */
        req.fail = REQ_FAIL;
        while (!req.cancel && now_ms() < end) {
            unsigned set[32] = { 0 }; /* fd_set */
            int tv[2] = { 0, 100000 };
            set[fd / 32] = 1u << fd % 32;
            int writing = sent < req.len;
            if ((k = select(fd + 1, writing ? 0 : set, writing ? set : 0, 0, tv)) <= 0) {
                if (k) break; /* not a timeout */
                continue;
            }
            if (writing) {
                k = send(fd, req.buf + sent, req.len - sent, 0x4000); /* MSG_NOSIGNAL */
                if (k <= 0) break;
                if ((sent += (unsigned)k) == req.len) spot_shut(fd);
                continue;
            }
            if ((k = recv(fd, req.buf + req.n, REPLY_MAX + 1 - req.n, 0)) <= 0 ||
                (req.n += (unsigned)k) > REPLY_MAX) {
                req.fail = k ? REQ_FAIL : REQ_OK;
                break;
            }
        }
    }
    if (fd >= 0) close(fd);
    if (req.n <= REPLY_MAX) req.buf[req.n] = 0; /* past the reply, the request's bytes */
    req.done = 1;
    return 0;
}

/* The request in flight dropped: its pthread joined (within 100 ms) and its buffer freed. */
static void req_cancel(void) {
    unsigned none = 0;
    worker_stop(req.thread, &req.running, &req.cancel, &none);
    free(req.buf);
    req.buf = 0;
    req.done = 0;
}

/* op and tail (its arguments: "\t" and escaped each) to the app at path, under a new id; the one
 * before is dropped. When it can't start, req.done is set at once, so the tick reports it. */
static void req_send(const char *path, const char *op, const char *tail) {
    req_cancel();
    ++req.id;
    req.cancel = 0;
    req.n = 0;
    req.fail = REQ_FAIL;
    req.path = path;
    int n = (req.buf = calloc(REPLY_MAX + 2, 1))
                ? tk_snprintf(req.buf, REQ_MAX, "Q2SPOT/1\n%u\t%s%s\n", req.id, op, tail)
                : 0;
    req.len = n > 0 && n < REQ_MAX ? (unsigned)n : 0;
    req.running = req.len && !pthread_create(&req.thread, 0, worker, 0);
    req.done = !req.running;
}

/* A finished request's thread joined: 1 when its reply (or failure) is ready to take. */
static int req_done(void) {
    if (!req.done) return 0;
    if (req.running) pthread_join(req.thread, 0);
    req.running = req.done = 0;
    return 1;
}

/* The page: addon_page, for app slot pg.a, one page_list rebuilt per level.
 * A level is an op and its arguments; only the top one keeps its reply, cut in place by spot_reply
 * into the header's 4 fields and 6 a row; Back asks again for the one below. A row's kind: list
 * opens the level uri (its context the argument), action sends uri with the context and toasts the
 * reply's message, search opens the T9 box (OK opens "search" for the text), remote opens the QR
 * code, local plays from the card (uri: a song, or a folder to list; context "album": in track
 * order), info does nothing; its position, when set, is the op a held Play/Pause sends.
 * A reply whose message is "refresh" is asked again every ADDON_LIVE_MS while its level is on top,
 * and redrawn only when it changed, its selected row kept. */
enum { K_LOAD, K_READY, K_ERROR, K_WAIT }; /* K_WAIT: no socket yet, the app starting */
enum { G_ROW, G_SHOW, G_SEARCH };

/* A level's page that can't show the app's rows (an error, or a load or start taking SLOW_MS) has a
 * Stop row whenever the app may be running, so a frozen one can always be ended. */
typedef struct {
    int state, n, sel, live, retry;
    int nosock, slow; /* nosock: the error is no socket; slow: the wait's Stop row shown */
    unsigned since;   /* when it began loading or waiting, 0 once done */
    unsigned base;     /* args' bytes before a More…'s cursor: the list's own */
    unsigned id, hash; /* id: the fetch whose reply this level takes; hash: its rows' bytes */
    char *buf;         /* the reply (the top level's), or 0 */
    char *f[4 + 6 * ADDON_ROWS];
    char title[128], note[128], op[16], args[400]; /* args: "\t" and escaped each */
} lv_t;

static struct {
    void *page, *view, *edit;
    unsigned timer, go, leave, again; /* again: when a waiting or live level asks again */
    int depth, act, next, arg, a,
        code; /* act: what the request in flight is for, 1 an action; a: the app's slot; code: its
                 QR code to show at the first answer */
    char term[64], aop[16];
    lv_t *lv; /* ADDON_DEPTH of them, calloc'd */
} pg __attribute__((section(".scratch")));

static void qr_open(const char *state, const char *title);
static app_t *app(void) { return &ap.a[pg.a]; }
static lv_t *top(void) { return &pg.lv[pg.depth - 1]; }
static const char *cell(const lv_t *l, int i, int k) { return l->f[4 + 6 * i + k]; }

/* "\t" and s escaped appended to out (size bytes); 0 when it doesn't fit. */
static int put(char *out, unsigned size, const char *s) {
    unsigned k = strlen(out);
    return k + 1 < size && (out[k] = '\t', spot_escape(s, out + k + 1, size - k - 1) >= 0);
}

static void fetch(lv_t *l) {
    pg.act = 0;
    req_send(app()->browse, l->op, l->args);
    l->id = req.id;
}

static void action(const char *op, const char *ctx) {
    char a[sizeof top()->args] = "";
    if (ctx[0] && !put(a, sizeof a, ctx)) return;
    tk_snprintf(pg.aop, sizeof pg.aop, "%s", op);
    req_send(app()->browse, op, a);
    pg.act = 1;
}

static int clicked(void *ctx, void *event);

/* Whether row i opens a page (go()): a list, the phone remote's QR page or a folder on the card. Its
 * row has a chevron. */
static int opens(lv_t *l, int i) {
    const char *kind = cell(l, i, 0), *uri = cell(l, i, 1);
    return !strcmp(kind, "list") || !strcmp(kind, "remote") ||
           (!strcmp(kind, "local") && *uri && uri[strlen(uri) - 1] == '/');
}

/* Whether the app may be running, so a Stop row can end it. */
static int stoppable(void) {
    return app()->launched || app()->restart || !access(app()->pid, 0);
}

static void stop_row(int i) {
    char t[48];
    tk_snprintf(t, sizeof t, "停止 %s", app()->name);
    page_row_detail(pg.view, i, t, 0, clicked);
}

/* The top level: its rows, or what it waits for. */
static void show(void) {
    lv_t *l = top();
    int n = 0;
    pg.view = pg.edit = 0;
    if (l->state == K_LOAD && !((req.running || req.done) && !pg.act && req.id == l->id)) fetch(l);
    if (l->state == K_LOAD || l->state == K_WAIT) {
        char t[48];
        tk_snprintf(t, sizeof t, "正在启动 %s…", app()->name);
        if (!l->since) l->since = time_now_ms() | 1;
        l->slow = (int)(time_now_ms() - l->since) >= SLOW_MS && stoppable(); /* since: now + 1 */
        void *v =
            page_list(pg.page, pg.page, 0, l->state == K_WAIT ? t : "加载中…", n = l->slow, 48);
        if (n) pg.view = v, stop_row(0);
    } else if (l->state == K_ERROR) {
        pg.view = page_list(pg.page, pg.page, 0, l->note, n = 1 + stoppable(), 48);
        page_row_detail(pg.view, 0,
                        l->retry ? "Retry" : "Install it again in System settings → Apps", 0,
                        clicked);
        if (n > 1) stop_row(1);
    } else {
        int more = l->f[3][0] != 0;
        pg.view = page_list(pg.page, pg.page, 0, l->title, n = l->n + more, 64);
        for (int i = 0; i < l->n; ++i)
            (opens(l, i) ? page_row_detail_into : page_row_detail)(pg.view, i, cell(l, i, 2), cell(l, i, 3), clicked);
        if (more) page_row_detail(pg.view, l->n, "更多…", "", clicked);
    }
    if (n) ringnav_select(pg.view, l->sel < n ? l->sel : n - 1, n);
}

/* The selected row kept, from a page showing the level's own rows (not an error or a wait's). */
static void keep_sel(void) {
    if (pg.view && top()->state == K_READY)
        top()->sel = widget_get_prop_int(pg.view, "_ringnav_index", 0);
}

/* A level over the top one, which lets its reply go (Back asks for it again); base: args' bytes that
 * name its list, before a More…'s cursor. */
static void push(const char *op, const char *args, unsigned base, const char *title) {
    lv_t next = { 0 };
    tk_snprintf(next.op, sizeof next.op, "%s", op);
    tk_snprintf(next.args, sizeof next.args, "%s", args);
    tk_snprintf(next.title, sizeof next.title, "%s", title);
    next.base = base < sizeof next.args ? base : 0;
    keep_sel();
    if (pg.depth == ADDON_DEPTH) { /* the oldest past the base forgotten */
        for (int i = 1; i < ADDON_DEPTH - 1; ++i) pg.lv[i] = pg.lv[i + 1];
        --pg.depth;
    }
    lv_t *l = top();
    free(l->buf);
    l->buf = 0, l->n = 0, l->state = K_LOAD;
    pg.lv[pg.depth++] = next;
    show();
}

static void pop(void) {
    req_cancel();
    free(top()->buf);
    --pg.depth;
    top()->state = K_LOAD;
    show();
}

/* The finished request: an action's toast, or the top level's rows. */
static void got(void) {
    lv_t *l = top();
    char id[12], *head[4], *g[4 + 6 * ADDON_ROWS], *reply = req.buf;
    /* The reply's bytes past its id, before spot_reply cuts them: the same as shown, nothing
     * redrawn. */
    unsigned skip = 9, hash = 0;
    while (req.fail == REQ_OK && skip < req.n && reply[skip] != '\t') ++skip;
    if (req.fail == REQ_OK && skip < req.n)
        hash = hash_bytes(FNV_SEED, (const unsigned char *)reply + skip, req.n - skip);
    int act = pg.act, n = req.fail == REQ_OK
                              ? spot_reply(reply, req.n, act ? head : g, act ? 0 : ADDON_ROWS)
                              : -1;
    char **f = act ? head : g;
    req.buf = 0;
    tk_snprintf(id, sizeof id, "%u", req.id);
    if (n >= 0 && strcmp(f[0], id)) n = -1;
    int ok = n >= 0 && !strcmp(f[1], "ok");
    unsigned now = time_now_ms();
    char why[48];
    tk_snprintf(why, sizeof why, req.fail == REQ_NOSOCK ? "%s isn't running" : "%s didn't answer",
                app()->name);
    pg.act = 0;
    if (act) {
        if (n >= 0 && f[2][0])
            toast(f[2]);
        else if (!ok)
            toast(why);
        if (ok && !strcmp(pg.aop, "stop")) {
            app()->launched = app()->hold = 0;
            navigator_back();
        } else
            keep_sel(), l->state = K_LOAD, show(); /* the level as the action left it */
    } else if (l->id != req.id) {
    } else if (req.fail == REQ_NOSOCK &&
               (app()->restart || now - app()->launch_at < ADDON_START_MS)) {
        if (l->state != K_WAIT && l->state != K_READY) l->state = K_WAIT, show();
        pg.again = now + 500;
    } else if (!ok) {
        l->state = K_ERROR;
        l->retry = 1;
        l->nosock = req.fail == REQ_NOSOCK;
        l->since = l->slow = 0;
        tk_snprintf(l->note, sizeof l->note, "%s",
                    req.fail != REQ_NOSOCK && n >= 0 && f[2][0] ? f[2] : why);
        show();
    } else {
        l->since = l->slow = 0;
        l->live = !strcmp(f[2], "refresh");
        pg.again = now + ADDON_LIVE_MS;
        if (l->state != K_READY || hash != l->hash) {
            keep_sel();
            free(l->buf);
            l->buf = reply, reply = 0;
            memcpy(l->f, f, sizeof l->f);
            l->n = n;
            l->hash = hash;
            l->state = K_READY;
            show();
        }
        if (pg.code) /* turned on by its row: its address at once, the menu behind it */
            pg.code = 0, qr_open(app()->state, app()->name);
    }
    free(reply);
}

static int go(const void *unused);

static void later(int next, int arg) {
    pg.next = next, pg.arg = arg;
    if (pg.page) rearm(&pg.go, go, 0); /* not once closed: the box's blur can come after */
}

static int clicked(void *ctx, void *event) {
    (void)event;
    later(G_ROW, (int)(long)ctx);
    return 0;
}

static int searched(void *ctx, void *event) {
    (void)ctx, (void)event;
    if (widget_get_text_utf8(pg.edit, pg.term, sizeof pg.term)) pg.term[0] = 0;
    later(pg.term[0] ? G_SEARCH : G_SHOW, 0);
    return 0;
}

static int search_left(void *ctx, void *event) {
    (void)ctx, (void)event;
    if (!pg.go) later(G_SHOW, 0);
    return 0;
}

/* A row's choice, on a timer: a row's click may not rebuild the list it is in. */
static int go(const void *unused) {
    (void)unused;
    lv_t *l = top();
    int i = pg.arg;
    char a[sizeof l->args] = "";
    pg.go = 0;
    if (pg.next == G_SHOW)
        show();
    else if (pg.next == G_SEARCH) {
        if (put(a, sizeof a, pg.term)) push("search", a, strlen(a), pg.term);
    } else if (l->state == K_ERROR ? i == 1 : l->state != K_READY && l->slow) { /* Stop <name> */
        char t[48];
        tk_snprintf(t, sizeof t, "%s stopped", app()->name);
        app_stop(app(), 0);
        toast(t);
        navigator_back();
    } else if (l->state == K_ERROR) {
        if (l->retry) {
            if (l->nosock && !access(app()->pid, 0)) /* running, its socket gone: started again */
                app_stop(app(), 1);
            else if (!app()->launched || access(app()->pid, 0))
                launch(app());
            l->state = K_LOAD, show();
        }
    } else if (l->state != K_READY) {
    } else if (i == l->n) { /* More…: the list's arguments and the cursor, as Q2SPOT/1's last */
        tk_snprintf(a, sizeof a, "%.*s", (int)l->base, l->args);
        if (put(a, sizeof a, l->f[3])) push(l->op, a, l->base, l->title);
    } else {
        const char *kind = cell(l, i, 0), *uri = cell(l, i, 1), *ctx = cell(l, i, 4);
        if (!strcmp(kind, "list")) { /* with remote and a local folder, opens()'s pages */
            if (!ctx[0] || put(a, sizeof a, ctx)) push(uri, a, strlen(a), cell(l, i, 2));
        } else if (!strcmp(kind, "action"))
            action(uri, ctx);
        else if (!strcmp(kind, "remote"))
            qr_open(app()->state, app()->name);
        else if (!strcmp(kind, "local")) /* a song, or a folder of them, on the card */
            songs_open(uri, cell(l, i, 2), !strcmp(ctx, "album"));
        else if (!strcmp(kind,
                         "search")) { /* peq_ui.c's T9 edit over the title, its keyboard open */
            keep_sel();
            widget_destroy_children(pg.page), pg.view = 0; /* the box alone, as Spotify's */
            pg.edit = edit_create(pg.page, "text", 2, 26);
            widget_set_prop_str(pg.edit, "tips", app()->tips);
            widget_on(pg.edit, EVT_VALUE_CHANGED, searched, 0);
            widget_on(pg.edit, EVT_IM_ACTION, searched, 0);
            widget_on(pg.edit, EVT_BLUR, search_left, 0);
            widget_set_focused(pg.edit, 1);
        }
    }
    return 0;
}

/* The page's timer: a finished request taken; a starting app, or a live level on top, asked
 * again. */
static int tick(const void *unused) {
    (void)unused;
    lv_t *l = top();
    unsigned now = time_now_ms();
    if (req_done())
        got();
    else if ((l->state == K_LOAD || l->state == K_WAIT) && !l->slow && l->since &&
             (int)(now - l->since) >= SLOW_MS && stoppable())
        show();
    else if (!req.running && (int)(now - pg.again) >= 0 && !pg.edit &&
             (l->state == K_WAIT || (l->state == K_READY && l->live && g_backlight_status &&
                                     window_manager_get_top_window(window_manager()) == pg.page)))
        fetch(l);
    return 8; /* RET_REPEAT */
}

static int leave(const void *unused) {
    (void)unused;
    pg.leave = 0;
    if (pg.edit) /* the search box emptied, so no search; its blur shuts the keyboard, then the box
                  */
        widget_set_text_utf8(pg.edit, ""), widget_set_focused(pg.edit, 0);
    else if (pg.depth > 1)
        pop();
    else
        navigator_back();
    return 0;
}

static int keyup(void *ctx, void *event) {
    (void)ctx;
    return return_key(event, &pg.leave, leave);
}

static int closed(void *ctx, void *event) {
    (void)ctx, (void)event;
    req_cancel();
    stop_timer(&pg.timer);
    stop_timer(&pg.go);
    stop_timer(&pg.leave);
    while (pg.depth) free(pg.lv[--pg.depth].buf);
    free(pg.lv);
    memset(&pg, 0, sizeof pg);
    return 0;
}

/* An app's Streaming row (navigation.c ringnav_stream; ctx: its place by name at the last scan):
 * the app started if it isn't, and its page; gone from the card since, the page says so. */
int addon_open(void *ctx, void *event) {
    (void)event;
    int k = (int)(long)ctx;
    if (k < 0 || k >= ap.shown || pg.page || !(pg.lv = calloc(ADDON_DEPTH, sizeof *pg.lv)))
        return 0;
    if (!(pg.page = page_open("addon_page", closed, keyup))) return free(pg.lv), pg.lv = 0, 0;
    pg.a = ap.order[k];
    app_t *a = app();
    int missing = access(a->bin, 0);
    if (!missing && (!a->launched || access(a->pid, 0))) launch(a), pg.code = a->qr;
    pg.depth = 1;
    lv_t *l = top();
    tk_snprintf(l->op, sizeof l->op, "menu");
    tk_snprintf(l->title, sizeof l->title, "%s", a->name);
    if (missing) {
        l->state = K_ERROR;
        tk_snprintf(l->note, sizeof l->note, "%s isn't on the card", a->name);
    }
    pg.timer = timer_add(tick, 0, 250);
    show();
    return 0;
}

/* ringnav_keylong: Play/Pause held on a row with a hold op (a list: all of it downloaded) sends it;
 * 1 when it was one. */
int addon_hold(void *w) {
    lv_t *l = pg.page && w == pg.page ? top() : 0;
    int i = l && l->state == K_READY && pg.view ? widget_get_prop_int(pg.view, "_ringnav_index", -1)
                                                : -1;
    if (i < 0 || i >= l->n || !cell(l, i, 5)[0]) return 0;
    action(cell(l, i, 5), cell(l, i, 4));
    return 1;
}

/* The QR code page (qrcode_page): an app's status file's qr (qrsize rows of hex, the leftmost
 * module the high bit), drawn on a white plate, beside its address (addr), PIN (pin) and note,
 * read again every second. Without an address (no Wi-Fi) or a file (not running), it says so. */
#define QR_RAW 1536
#define QR_HEX 640
static struct {
    void *page, *qr, *col, *msg, *addr, *pin, *note, *say, *sub;
    unsigned timer, leave, seen; /* seen: the state file as last read, hashed */
    const char *state;
    int size;
    char *hex; /* the code's rows, QR_HEX bytes calloc'd with the page */
} qp __attribute__((section(".scratch")));

static void set(void *w, const char *s) {
    if (tk_strcmp(widget_get_prop_str(w, "text", ""), s)) widget_set_text_utf8(w, s);
}

static void qr_refresh(void) {
    char raw[QR_RAW], addr[64], pin[16], note[128], spaced[16];
    int n = (int)state_read(qp.state, raw, sizeof raw);
    unsigned h = hash_bytes(FNV_SEED, (const unsigned char *)raw, (unsigned)n + 1);
    if (h == qp.seen) return;
    qp.seen = h;
    field(raw, "addr", addr, sizeof addr);
    field(raw, "pin", pin, sizeof pin);
    field(raw, "note", note, sizeof note);
    field(raw, "qr", qp.hex, QR_HEX);
    qp.size = (int)number(raw, "qrsize");
    int w = (qp.size + 3) / 4,
        code = addr[0] && qp.size >= 21 && qp.size <= 177 && (int)strlen(qp.hex) == w * qp.size;
    widget_set_visible(qp.qr, code, 0);
    widget_set_visible(qp.col, code, 0);
    widget_set_visible(qp.msg, !code, 0);
    if (!code) {
        set(qp.say, !n ? "Not running" : addr[0] ? "No code yet" : "Connect the Q2 to Wi-Fi");
        set(qp.sub, !n ? "Open it again" : addr[0] ? "Wait a moment" : "Then come back here");
        return;
    }
    if (strlen(pin) == 6) /* 548 001 */
        memcpy(spaced, pin, 3), spaced[3] = ' ', memcpy(spaced + 4, pin + 3, 4);
    widget_set_prop_int(qp.addr, "style:normal:font_size",
                        strlen(addr) > 17 ? 14 : 16); /* 192.168.100.200:8080 */
    set(qp.addr, addr);
    set(qp.pin, strlen(pin) == 6 ? spaced : pin);
    set(qp.note, note);
    widget_invalidate_force(qp.qr, 0);
}

static int qr_tick(const void *unused) {
    (void)unused;
    if (g_backlight_status) qr_refresh();
    return 8; /* RET_REPEAT */
}

static int qr_leave(const void *unused) {
    (void)unused;
    qp.leave = 0;
    navigator_back();
    return 0;
}

static int qr_keyup(void *ctx, void *event) {
    (void)ctx;
    return return_key(event, &qp.leave, qr_leave);
}

static int qr_closed(void *ctx, void *event) {
    (void)ctx, (void)event;
    stop_timer(&qp.timer);
    stop_timer(&qp.leave);
    free(qp.hex);
    memset(&qp, 0, sizeof qp);
    return 0;
}

static void qr_open(const char *state, const char *title) {
    if (qp.page || !(qp.hex = calloc(QR_HEX, 1))) return;
    if (!(qp.page = page_open("qrcode_page", qr_closed, qr_keyup))) {
        free(qp.hex), qp.hex = 0;
        return;
    }
    void *f = widget_factory(), *page = qp.page;
    int h = widget_get_prop_int(page, "h", 290);
    qp.state = state;
    widget_set_text_utf8(label(page, 16, 12, 240, 16, "s_scrlabel_white20l", 16, SPOT_GREY), title);
    qp.qr = widget_factory_create_widget(f, "view", page, 16, 40, QR_PX, QR_PX);
    qp.col = widget_factory_create_widget(f, "view", page, QR_COL, 40, 375 - QR_COL - 12, QR_PX);
    widget_set_text_utf8(label(qp.col, 0, 2, 140, 22, "s_scrlabel_white20l", 18, 0xffffffffu),
                         "Scan with your");
    widget_set_text_utf8(label(qp.col, 0, 24, 140, 22, "s_scrlabel_white20l", 18, 0xffffffffu),
                         "phone's camera");
    widget_set_text_utf8(label(qp.col, 0, 60, 140, 18, "s_scrlabel_white20l", 14, SPOT_GREY),
                         "or open");
    qp.addr = label(qp.col, 0, 78, 375 - QR_COL - 12, 22, "s_scrlabel_white20l", 16, 0xffffffffu);
    widget_set_text_utf8(label(qp.col, 0, 112, 140, 18, "s_scrlabel_white20l", 14, SPOT_GREY),
                         "PIN");
    qp.pin = label(qp.col, 0, 130, 140, 28, "s_scrlabel_white20l", 24, 0xffffffffu);
    qp.note = label(qp.col, 0, 172, 375 - QR_COL - 12, 20, "s_scrlabel_white20l", 14, SPOT_GREY);
    void *scroll[] = { qp.addr, qp.note };
    for (unsigned i = 0; i < 2; ++i)
        hscroll_label_set_only_focus(scroll[i], 0), hscroll_label_set_ellipses(scroll[i], 0);
    qp.msg = widget_factory_create_widget(f, "view", page, 0, 0, 375, h);
    qp.say =
        label(qp.msg, CF_EDGE, 100, 375 - 2 * CF_EDGE, 30, "s_scrlabel_white20c", 20, 0xffffffffu);
    qp.sub =
        label(qp.msg, CF_EDGE, 134, 375 - 2 * CF_EDGE, 24, "s_scrlabel_white20c", 16, SPOT_GREY);
    qp.seen = 0; /* nothing read yet: no read hashes to 0 but by chance, and then only once */
    qr_refresh();
    qp.timer = timer_add(qr_tick, 0, 1000);
}

static void plate(void *canvas, int x, int y, int w, int h, unsigned color, unsigned radius) {
    int r[4] = { x, y, w, h };
    if (canvas_fill_rounded_rect(canvas, r, (void *)0, &color, radius)) {
        canvas_set_fill_color(canvas, color);
        canvas_fill_rect(canvas, x, y, w, h);
    }
}

/* ringnav_paint, every widget after stock painted it: the code, dark modules on the white plate, a
 * row's run of them one rectangle, inside a quiet zone of two modules. */
void addon_paint(void *w, void *canvas) {
    if (!qp.page || w != qp.qr || !P(canvas, CANVAS_LCD) || qp.size < 21) return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    int n = qp.size, m = QR_PX / (n + 4), o = (QR_PX - m * n) / 2, digits = (n + 3) / 4;
    plate(canvas, 0, 0, QR_PX, QR_PX, 0xffffffffu, 10);
    canvas_set_fill_color(canvas, rgba(0x000000, 255));
    for (int y = 0; y < n; ++y)
        for (int x = 0; x < n;) {
            int run = 0;
            for (; x + run < n; ++run) {
                int d = hex(qp.hex[y * digits + (x + run) / 4]);
                if (d < 0 || !(d >> (3 - (x + run) % 4) & 1)) break;
            }
            if (run) canvas_fill_rect(canvas, o + x * m, o + y * m, run * m, m);
            x += run ? run : 1;
        }
    stock_canvas_set_fill_color(canvas, fill);
}
