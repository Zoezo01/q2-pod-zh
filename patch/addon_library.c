/* Card apps' library update (addon.c's apps): after a sync an app asks for Update Local Music and
 * the import of its own playlists in /tmp/q2-<id>.library, written whole (temp + rename): "<seq>"
 * on its first line (23 bytes at most), then "scan" (Update Local Music), "import <path>" and
 * "add <path>" lines, LF or CRLF, each path a playlist directly in LIB_DIR whose name is
 * LIB_NAME - 1 bytes at most, LIB_MAX bytes in all. library_poll, once per addon_poll pass, takes a
 * seq new for its app, any app on the card, running or not, from one read of the file. A new seq is
 * checked whole first: any error is its answer at once and nothing runs. Then the scan, then
 * stock's Import Playlists (importPlayList, which replaces a playlist of the same name) limited to
 * the files named (addon_m3u_dir), each once library_safe() lets it; an add's file only if no
 * playlist has its name yet. The answer, "<seq> ok" or "<seq> error <why>", then "exists <path>"
 * for each add left alone, goes to .library.done. One request at a time; a newer seq waits. */
#include "offsets.inc"
#include "peq.h"
#include "stock.h"

#define LIB_DIR "/mnt/mmc/_explaylist_data/"
#define LIB_MAX (32 << 10) /* a request's bytes: over 300 imports of the longest names */
#define LIB_POLL_MS 500    /* as addon.c's ADDON_POLL_MS */
/* The answer's bytes: its first line, then an exists line per add left alone, each 3 bytes longer
 * than its request line, which is 36 bytes at least. */
#define LIB_ANSWER (LIB_MAX + LIB_MAX / 8)
#define LIB_TMP "/tmp/q2-library.tmp"
/* A playlist's name, its file's less .m3u, is LIB_NAME - 1 bytes at most: stock's create
 * (ringnav_playlist_create) cuts a longer one into char title[64] and skips its duplicate check,
 * so the playlist's name would never match the file's again and each import would add a copy. */
#define LIB_NAME 64

/* only and busy are first, as test/patch.py reads them. */
static struct {
    const char *only; /* libup.list while importPlayList runs; else 0 */
    int busy;         /* in library_poll: a dialog's own loop (a toast's) polls again */
    char seq[24];     /* the request running */
    char done[48];    /* its .done path */
    /* LIB_MAX + LIB_ANSWER bytes of heap, taken at the first request and kept: the request as
     * read, then its files moved to its start, each its op and its name in LIB_DIR, NUL-ended, an
     * empty one last; the answer after LIB_MAX. op: 'i' imported, 'a' an add not looked up yet,
     * 'e' an add whose name a playlist has (left alone: an exists line). */
    char *list;
    int state, scan; /* state: L_IDLE, L_WAIT (to scan), L_SCAN (scanning), L_IMPORT */
    int seen;        /* L_SCAN: its scan seen running (scan_seen) */
    unsigned polled; /* library_poll's last pass */
    /* Each app's last seq taken, by id: as many as addon.c's slots, which it never frees either. */
    struct {
        char id[16], seq[24];
    } taken[APPS_MAX];
} libup __attribute__((section(".scratch")));
enum { L_IDLE, L_WAIT, L_SCAN, L_IMPORT };

/* The scan a request opened, seen: its thread running, or its dialog (dialog/updatemusic_dialog,
 * which starts the thread) still open. */
static int scan_seen(void) {
    return library_scanning() || navigator_window_is_exist("updatemusic_dialog");
}

/* "<seq> ok" (why 0) or "<seq> error <why>", then "exists <path>" for each add left alone, built
 * whole and written to libup.done through LIB_TMP (blob_io: renamed over only once all of it is
 * written, so a full /tmp never publishes exists lines cut short, or the app would take a user's
 * same-named playlist for its own); the request ends. If that fails, "<seq> error Couldn't write
 * the answer" alone; if not even that, nothing (the app keeps waiting). */
static void library_done(const char *why) {
    char *b = libup.list + LIB_MAX;
    int n = why ? tk_snprintf(b, LIB_ANSWER, "%s error %s\n", libup.seq, why)
                : tk_snprintf(b, LIB_ANSWER, "%s ok\n", libup.seq);
    for (const char *l = libup.list; *l; l += strlen(l) + 1)
        if (*l == 'e') n += tk_snprintf(b + n, LIB_ANSWER - n, "exists " LIB_DIR "%s\n", l + 1);
    if (!blob_io(libup.done, LIB_TMP, b, n, 1)) {
        n = tk_snprintf(b, LIB_ANSWER, "%s error Couldn't write the answer\n", libup.seq);
        if (!blob_io(libup.done, LIB_TMP, b, n, 1)) unlink(LIB_TMP);
    }
    libup.state = L_IDLE;
}

/* s's first line ended at its "\n", or a "\r\n"; the next line's start. */
static char *line_end(char *s) {
    char *next = strstr(s, "\n");
    if (next)
        *next++ = 0;
    else
        next = s + strlen(s);
    unsigned k = strlen(s);
    if (k && s[k - 1] == '\r') s[k - 1] = 0;
    return next;
}

/* The request's lines after its seq, at s in libup.list: scan into libup.scan, each file moved to
 * the list (before its own line: it is shorter) with its op; what is wrong, else 0. */
static const char *library_parse(char *s, char *why, unsigned size) {
    char *w = libup.list;
    for (char *next; *s; s = next) {
        next = line_end(s);
        if (!*s) continue;
        if (!strcmp(s, "scan")) {
            libup.scan = 1;
            continue;
        }
        int add = tk_str_start_with(s, "add ");
        if (!add && !tk_str_start_with(s, "import ")) {
            tk_snprintf(why, size, "Unknown request: %s", s);
            return why;
        }
        char *p = s + (add ? 4 : 7), *name = p + sizeof LIB_DIR - 1;
        unsigned k = strlen(p);
        /* stock lists that folder alone, by name: no subfolder, a name before .m3u (so never ..) */
        if (!tk_str_start_with(p, LIB_DIR) || strstr(name, "/") || k < sizeof LIB_DIR + 4 ||
            strcasecmp(p + k - 4, ".m3u")) {
            tk_snprintf(why, size, "Not a playlist in /mnt/mmc/_explaylist_data: %s", p);
            return why;
        }
        if (k - (sizeof LIB_DIR - 1) - 4 >= LIB_NAME) {
            p[k - 4] = 0;
            tk_snprintf(why, size, "Name too long: %s", name);
            return why;
        }
        if (access(p, 0)) {
            tk_snprintf(why, size, "Not found: %s", p);
            return why;
        }
        *w++ = add ? 'a' : 'i';
        for (k = 0; name[k]; ++k) w[k] = name[k];
        w[k] = 0, w[k + 1] = 0;
        w += k + 1;
    }
    return 0;
}

/* Whether Playlists' list (p_deque_playlist) has a listed file's playlist: its name before .m3u,
 * which every listed name ends in, as stock's listing (toolsGetDirectoryM3u8) cuts the file's name
 * at its last dot. */
static int playlist_named(const char *name) {
    void *pl = P(p_deque_playlist, 0);
    unsigned k = strlen(name) - 4;
    for (unsigned i = 0; pl && i < deque_size(pl); ++i) {
        const char *n = P(deque_at(pl, i), REC_NAME);
        if (n && strlen(n) == k && tk_str_start_with(name, n)) return 1;
    }
    return 0;
}

/* Each add's name looked up in Playlists' list, loaded: one a playlist has already is left alone
 * ('e'), the others imported ('i'); whether any file is left to import. */
static int library_adds(void) {
    int any = 0;
    for (char *l = libup.list; *l; l += strlen(l) + 1) {
        if (*l == 'a') *l = playlist_named(l + 1) ? 'e' : 'i';
        any |= *l == 'i';
    }
    return any;
}

/* After the import, the first imported file whose playlist isn't in Playlists' list (stock reloads
 * it), as why; else 0. Stock stops at a create that fails (a database error, the 100 playlists'
 * cap) and returns as if done. */
static const char *library_missing(char *why, unsigned size) {
    for (const char *l = libup.list; *l; l += strlen(l) + 1) {
        if (*l != 'i' || playlist_named(l + 1)) continue;
        /* the name less its .m3u */
        tk_snprintf(why, size, "Couldn't import %.*s", (int)strlen(l + 1) - 4, l + 1);
        return why;
    }
    return 0;
}

/* Whether the scan or the import may run now: nothing plays (media_playing: the player, Spotify,
 * radio, a video; or AirPlay, or the Bluetooth receiver, as ringnav_sleep's quiet check has it;
 * stock's scan dialog stops the player), no scan runs, and every open window, the window manager's
 * children less the status bar, is one known to hold no library records: Home, Streaming and its
 * Configuration, a card app's page and its QR code, Now Playing, and System and Playback settings'
 * own pages. Any other waits, a dialog's (this poll runs inside its own loop, a toast's too: no
 * request starts there), a Library page's (the import reloads p_deque_playlist and
 * p_deque_showlist under it; Q2 Pod's own keep records read from the database the scan rebuilds),
 * the user's own Update Local Music, and a page this list doesn't know. */
static int library_safe(void) {
    static const char *const safe[] = {
        "home_page",      "stream_page",       "streamconf_page",    "addon_page",
        "qrcode_page",    "playing_page",      "sysset_page",        "display_page",
        "backlight_page", "lighttime_page",    "keylock_page",       "language_page",
        "datetime_page",  "autotime_page",     "manualtime_page",    "screensaver_page",
        "standby_page",   "powermanager_page", "sleepshutdown_page", "about_page",
        "bluetooth_page", "btquality_page",    "btadjustvol_page",   "wifi_page",
        "wifiset_page",   "netservice_page",   "playset_page",       "audiosetting_page",
        "playmode_page",  "replaygain_page",   "filter_page",        "dsdoutput_page",
        "memplay_page",   "usbvol_page",       "maxvol_page",        "bootvol_page",
        "balance_page",   "equalizer_page"
    };
    if (media_playing() || airplayGetFlag() == 2 || (signed char)bt__recv_pageflag == 2 ||
        library_scanning())
        return 0;
    void *wm = window_manager(), *bar = *(void *const *)system_bar;
    for (unsigned i = 0, n = widget_count_children(wm); i < n; ++i) {
        void *w = widget_get_child(wm, i);
        const char *name = widget_get_prop_str(w, "name", "");
        unsigned k = 0;
        while (k < sizeof safe / sizeof *safe && tk_strcmp(name, safe[k])) ++k;
        if (w != bar && k == sizeof safe / sizeof *safe) return 0;
    }
    return 1;
}

/* The last seq taken from app id. */
static char *taken(const char *id) {
    for (int i = 0; i < APPS_MAX; ++i) {
        if (!libup.taken[i].id[0])
            tk_snprintf(libup.taken[i].id, sizeof libup.taken[i].id, "%s", id);
        if (!strcmp(libup.taken[i].id, id)) return libup.taken[i].seq;
    }
    return 0;
}

/* A request that never runs, its seq missing or too long, answered why once: written only while
 * .done doesn't hold that answer. */
static void library_refuse(const char *seq, const char *why) {
    char line[96], old[sizeof line];
    int k = tk_snprintf(line, sizeof line, "%s error %s\n", seq, why);
    state_read(libup.done, old, sizeof old);
    if (strcmp(old + 1, line)) blob_io(libup.done, LIB_TMP, line, k, 1);
}

/* App id's request, if it has a new one: taken, or answered at once. Every poll reads its first
 * line alone; a new seq there reads the whole file, once, and that read's own first line must be
 * the seq: a file gone since, or replaced by a newer seq, is looked at again next poll, so no seq
 * runs another's lines and none is answered without its own. */
static void library_take(const char *id) {
    char path[48], head[64], why[600], *seq = head + 1, *last = taken(id);
    tk_snprintf(path, sizeof path, "/tmp/q2-%s.library", id);
    if (!last || !state_read(path, head, sizeof head)) return;
    line_end(seq); /* the whole file when it has no newline */
    tk_snprintf(libup.done, sizeof libup.done, "%s.done", path);
    if (!*seq || strlen(seq) >= sizeof libup.seq) { /* can't be kept: never run, answered once */
        library_refuse(seq, *seq ? "Seq too long" : "Missing seq");
        return;
    }
    if (!strcmp(seq, last)) return; /* the one taken last is done already */
    /* answered before demo restarted (Rockbox from Home): /tmp outlives taken[] */
    char old[64];
    unsigned k = strlen(seq);
    state_read(libup.done, old, sizeof old);
    if (tk_str_start_with(old + 1, seq) && old[1 + k] == ' ') {
        tk_snprintf(last, sizeof libup.seq, "%s", seq);
        return;
    }
    if (!libup.list && !(libup.list = calloc(1, LIB_MAX + LIB_ANSWER))) return; /* next poll */
    unsigned n = state_read(path, libup.list, LIB_MAX);
    char *body = line_end(libup.list + 1);
    if (!n || strcmp(libup.list + 1, seq)) return;
    tk_snprintf(last, sizeof libup.seq, "%s", seq);
    tk_snprintf(libup.seq, sizeof libup.seq, "%s", seq);
    libup.scan = 0;
    libup.list[0] = 0; /* no files until library_parse moves them there */
    const char *bad = n >= LIB_MAX - 2            ? "Request too long"
                      : access("/tmp/mmc_add", 0) ? "No memory card"
                                                  : library_parse(body, why, sizeof why);
    if (bad || (!libup.scan && !libup.list[0])) { /* a seq alone: nothing to do */
        library_done(bad);
        return;
    }
    libup.state = libup.scan ? L_WAIT : L_IMPORT;
}

static void library_step(void) {
    const char *id;
    for (int k = 0; libup.state == L_IDLE && (id = addon_id(k)); ++k) library_take(id);
    if (libup.state == L_WAIT) {
        /* the scan's dialog stops the player: a song paused under a lit screen keeps its place */
        if (!library_safe() || (mclGetPlayStatus() == 3 && g_backlight_status)) return;
        /* first: the dialog runs a loop of its own, whose polls look for the scan (library_poll) */
        libup.state = L_SCAN, libup.seen = 0;
        int ctx[4] = { 0 };
        navigator_to_with_context("dialog/updatemusic_dialog", ctx);
        libup.seen |= scan_seen();
        return;
    }
    if (libup.state == L_SCAN) {
        /* Over once the scan was seen and is gone; one never seen (its dialog didn't open) is an
         * error, never an ok without a scan. ponytail: a scan cancelled with the dialog's Back
         * still reports ok (stock keeps no cancel flag). */
        if (scan_seen()) {
            libup.seen = 1;
            return;
        }
        if (!libup.seen) {
            library_done("Couldn't start the scan");
            return;
        }
        if (!libup.list[0]) {
            library_done(0);
            return;
        }
        libup.state = L_IMPORT;
    }
    if (libup.state != L_IMPORT) return;
    if (!library_safe()) return;
    /* Stock's replace finds a same-named playlist in p_deque_playlist alone (checkPlayListByName),
     * empty until Library → Playlists first loads it; otherwise the create fails on the name
     * (ringnav_playlist_create) and the songs are added to no playlist.
     * ponytail: the import reads every song's tags on the UI thread, as stock's Import row does. */
    ringnav_localclass(0xf00b);
    if (!library_adds()) { /* only adds, each's playlist there already: nothing is imported */
        library_done(0);
        return;
    }
    libup.only = libup.list;
    int r = importPlayList();
    libup.only = 0;
    char why[300];
    const char *bad = r == -2 ? "No memory card" : library_missing(why, sizeof why);
    library_done(bad); /* before the toast, whose dialog runs a loop of its own */
    toast(bad ? bad : "Playlists updated");
}

/* addon_poll's, once a pass, every LIB_POLL_MS. A stock dialog it opens (Update Local Music, a
 * toast) runs a loop of its own, which polls again: that nested poll runs nothing, so no request
 * runs twice; it only notes the scan it sees (the scan dialog's call returns once it is over). */
void library_poll(void) {
    if (libup.busy) {
        if (libup.state == L_SCAN && scan_seen()) libup.seen = 1;
        return;
    }
    unsigned now = time_now_ms();
    if (now - libup.polled < LIB_POLL_MS) return;
    libup.polled = now;
    libup.busy = 1;
    library_step();
    libup.busy = 0;
}

/* Whether path is one of the files the import imports: in LIB_DIR, its name an 'i' one's. */
static int listed(const char *path) {
    if (!tk_str_start_with(path, LIB_DIR)) return 0;
    for (const char *l = libup.only; *l; l += strlen(l) + 1)
        if (*l == 'i' && !strcmp(l + 1, path + sizeof LIB_DIR - 1)) return 1;
    return 0;
}

/* M3U_DIR_CALL: importPlayList's listing of LIB_DIR's playlists; while a card app's request
 * imports, only the files it named, so the user's own exports there aren't imported again over
 * playlists edited since. Stock's own Import Playlists row gets the whole folder. */
int addon_m3u_dir(const char *dir, void *dq) {
    int n = toolsGetDirectoryM3u8(dir, dq);
    if (!libup.only) return n;
    void *kept = _create_deque("stSongInfo");
    deque_init(kept);
    for (unsigned i = 0; i < deque_size(dq); ++i) {
        void *r = deque_at(dq, i);
        const char *p = P(r, REC_PATH);
        if (p && listed(p)) _deque_push_back(kept, r);
    }
    deque_clear(dq);
    deque_assign(dq, kept);
    deque_destroy(kept);
    return (int)deque_size(dq);
}
