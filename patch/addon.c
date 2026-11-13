/* Card apps: programs that run from the card beside Q2 Pod, installed by System
 * settings' Apps from their own GitHub releases (netupdate.c) or unzipped to the card by hand, as
 * Spotify's librespot is. Q2 Pod knows none of them by name: each is a card folder .<id> holding
 *   q2app    its manifest, key=value lines: name (its Streaming row's), search (the search box's
 *            hint), qr (1: its row turns it on and shows its QR code once it answers), scrobble
 *            (0: its songs stay out of the scrobble log, addon_scrobbles) and stop (0: its page has
 *            no Stop row, and an error page's second row is Cancel);
 *   run      its launcher, which copies the app to /tmp and runs it there in a restart loop, so
 *            the card is never kept busy, leaving the loop's pid in /tmp/q2-<id>;
 *   q2-<id>  the app;
 *   icon-40.png, icon.png
 *            its Streaming row's icon, 40 px (SET_ICON), drawn 1:1, else 52 px, scaled to fit;
 *            one over ART_MAX px either way counts as none; without them, the shared stream_app.
 * Streaming lists those on the card (home.c ringnav_stream). A row starts its app and opens
 * addon_page, a list the app draws over Q2SPOT/1 (docs/librespot.md) on /tmp/q2-<id>.browse.sock:
 * each row's kind says what a press does (a local row plays a song or folder from the card,
 * library_lists.c's songs_open). Its status file /tmp/q2-<id>.state says whether to hold standby off
 * (hold=1: files moving, a phone using its page) and carries its web page's QR code, address and
 * PIN, which qr_open's page draws. A TERM to the loop's pid asks it to end. An app that plays as
 * well (q2-deezer's Stream mode) names a card folder of songs in /tmp/q2-<id>.play, which
 * stream_poll plays as it fills, or an .m3u of card paths, which it plays in order, waiting for
 * each, or its Q2URLS/1 queue of stream URLs, /tmp/q2-<id>.urls, which stock's Tidal player plays
 * (tidal.c app_urls_play). It asks for Update Local Music and its playlists' import in
 * /tmp/q2-<id>.library (addon_library.c), the result in .library.done. A list reply whose message
 * is "pages" and that has a cursor is followed by its next pages, appended to the same list
 * (page_next), up to ADDON_PAGES.
 *
 * Go to Album and Go to Artist on a song in an app's folder open the app's own page for them when
 * its q2app says goto=1 (addon_goto), and every song an app queues plays with its file's tags
 * (tags.c card_tags).
 *
 * An app's /tmp/q2-<id>.sleep asks for a sleep timer: playback pauses at a time, or as the track
 * playing ends (sleep_read, sleep_check).
 *
 * A card game (q2app game=1, then no run or Streaming row) is a row in Library's Games instead:
 * it runs over the whole screen, as Videos' player does, until it exits (addon_game_start). One
 * with audio=1 as well owns the sound: the player is stopped and the headphone output readied
 * before it starts, as for a video, it gets the output device (and a soft output's volume) as
 * its arguments, Play/Pause and the side buttons reach it, and the idle quit is its own.
 *
 * An app's first page may be sections (sec_take): one shows at a time, and a sideways swipe moves
 * to the next or the previous (swipe).
 *
 * Covers ("art" in the caps): a row's cover is a JPEG the app leaves at /tmp/q2-<id>.art/<key>.jpg,
 * key the row's URI, a tab and its context, hashed FNV-1a 32 (hash_bytes from FNV_SEED) and written
 * as 8 lowercase hex digits. A list with a cover for any of its rows is drawn as stock Tidal's
 * lists are (art_row): ART_ROW_H rows, the cover at the left, the title and subtitle beside it; a
 * row without one keeps the cover's room empty. The app sizes the covers: one over ART_MAX px
 * either way counts as none. The protocol is unchanged, so an app without covers, or firmware without "art", shows the plain rows.
 *
 * The card-app features this firmware has are words in /tmp/q2-addon.caps (ADDON_CAPS), written at
 * every Streaming scan. */
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
#define PROGRESS_H 8            /* a progress row's bar: a flat strip under its text */
#define PROGRESS_GLOSS 17       /* ... or, under the scrubber's rows, all of their gloss */
#define PROGRESS_TRACK 0x3a3a3a /* its unfilled part, dim against the black row */
/* ponytail: replies a level appends (page_next), 500 rows, under ringnav's MAX_ENTRIES (512), which
 * walks every row per wheel event; 2,000 needs MAX_ENTRIES 2048 after measuring, or a table_client
 * page. */
#define ADDON_PAGES 10
#define REPLY_MAX (64 << 10) /* Q2SPOT/1's limits */
#define REQ_MAX (8 << 10)
#define AWAY_MS 3000     /* USB storage waits at most this long for an app to let the card go */
#define SLOW_MS 3000     /* a page loading or starting this long offers to stop the app */
#define BUSY_MS 400      /* an action unanswered this long shows Working… on its row */
#define RESTART_MS 20000 /* Retry's restart starts the app by then, its old loop gone or not */
#define QUIT_MS 20000    /* the Apps page's worker waits at most this long for an app to end */
#define APP_ID 16        /* an id's bytes, its NUL included */
#define QR_PX 186        /* the code's white plate: 5 px modules up to a 33-module code */
#define QR_COL 214       /* the text beside it */
#define QR_LIGHT_PX 166  /* Classic's plate: 5 px modules up to 29, 4 up to 37 */
#define QR_RAW 1536      /* an app's status file as far as it is read (poll_one, qr_refresh) */
#define CARD "/mnt/mmc"
#define CAPS_FILE "/tmp/q2-addon.caps"
/* This firmware's card-app features, space-separated; a feature adds its word. */
#define ADDON_CAPS                                                                                 \
    "playlist progress pages library goto sleep game audio sections art window urls sort"
#define ADDON_SECTIONS 8  /* sections an app's first page may have */
#define SWIPE_CLAIM_PX 24 /* a sideways move this far, twice as far as up or down, is a swipe's */
#define SWIPE_PX 60       /* and this far at the lift, more than up or down, changes section */
#define ART_ROW_H 78 /* a row with a cover: stock Tidal's list_view default_item_height */
#define ART_PX 64    /* its cover, square, centred in the row's height */
#define ART_MAX 128  /* the most a cover's or an icon's file may be, either way; larger is none */
#define ART_KEEP 24  /* the rows' covers kept decoded around the list's scroll position */
#define ART_URL (APP_ID + 32) /* art_file's file:// URL, its NUL included */

/* An app found on the card, kept for the life of demo in its slot. present: on the card at the
 * last scan; relaunch: stopped for USB storage, to start again; restart: stopped by Retry (its
 * socket gone), to start again once its loop is gone. */
typedef struct {
    char id[APP_ID], name[32], tips[32], bin[48], run[48], pid[32], state[40], browse[48], icon[56];
    int qr, game, audio, present, launched, hold, relaunch, restart;
    int nostop; /* q2app's stop=0: no Stop row on its page */
    unsigned polled, launch_at, restart_at, play; /* play: its .play as read last, hashed */
} app_t;

static struct {
    app_t a[APPS_MAX];
    int n, shown, order[APPS_MAX]; /* order: the slots on the card, by name */
    int games, game[APPS_MAX];     /* the card games among them, by name too */
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
        if (!a->tips[0]) tk_snprintf(a->tips, sizeof a->tips, "Search");
        a->qr = number(raw, "qr") == 1;
        a->game = number(raw, "game") == 1;
        a->audio = number(raw, "audio") == 1;
        char stop[4]; /* as text, as scrobble: number() is 0 for a missing key too */
        field(raw, "stop", stop, sizeof stop);
        a->nostop = !strcmp(stop, "0") || !strcmp(stop, "0\r");
        a->present = !access(a->bin, 0);
        /* the row's size first, the other scaled to fit (navigation.c set_child), else stream_app
         * (one over ART_MAX px either way, or whose size can't be read, counts as none: demo
         * decodes it whole) */
        static const char *const icons[2] = { "icon-40.png", "icon.png" };
        tk_snprintf(a->icon, sizeof a->icon, "stream_app");
        for (int i = 1; i >= 0; --i) {
            int w = 0, h = 0;
            tk_snprintf(path, sizeof path, CARD "/%s/%s", e->d_name, icons[i]);
            if (picture_size(path, &w, &h) && w && h && w <= ART_MAX && h <= ART_MAX)
                tk_snprintf(a->icon, sizeof a->icon, "file://%s", path);
        }
    }
    if (d) closedir(d);
    ap.shown = ap.games = 0;
    for (int i = 0; i < ap.n; ++i) { /* by name: an insertion, eight at most */
        if (!ap.a[i].present) continue;
        int *order = ap.a[i].game ? ap.game : ap.order, k = ap.a[i].game ? ap.games++ : ap.shown++;
        for (; k && strcasecmp(ap.a[order[k - 1]].name, ap.a[i].name) > 0; --k)
            order[k] = order[k - 1];
        order[k] = i;
    }
    blob_io(CAPS_FILE, CAPS_FILE ".tmp", ADDON_CAPS "\n", sizeof(ADDON_CAPS "\n") - 1, 1);
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

/* A pid file whose loop is gone (crashed, or killed) is removed, so no wait spends its limit on it.
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
 * order, as a local row's album is: songs_open), Now Playing opening as for a press. While the
 * queue is still that folder's, songs that come later (named after the last one queued) join its
 * end, as Add to Queue adds them (playback_queue_insert); the path of the one playing is left in
 * /tmp/q2-<id>.playing, so the app fetches only a few ahead and deletes the ones long played. Or
 * .play names an .m3u on the card (q2-subsonic's queue), one card path a line: its songs are queued
 * in its order, each waited for until it is there (m3u_songs). */
#define M3U_MAX (256 << 10) /* a playlist file read this far: about 2,000 card paths */
#define M3U_BATCH 32        /* folders listed a poll at most, so a long list doesn't stall the UI */
static struct {
    app_t *app;        /* whose .play it is */
    char dir[160];     /* its folder */
    char last[192];    /* the name of the last of its songs queued */
    char playing[512]; /* the .playing path written last */
    int state;       /* S_WAIT for its first song, S_PLAY its queue playing, S_DONE another queue */
    unsigned timer;  /* the first song's start, on a timer (a window opens) */
    long seen;       /* the folder's mtime at the last scan that came well after it */
    int m3u;         /* sm.dir is a playlist file */
    int urls;        /* sm.dir is the app's URL queue (tidal.c app_urls_play) */
    int next;        /* its entries passed so far */
    int more;        /* a batch filled, so the next poll goes on */
    char first[512]; /* the session's first song */
    char want[512];  /* the entry waited for */
    unsigned loads;  /* playback_loads at its start, then once its queue was loaded */
} sm __attribute__((section(".scratch")));
enum { S_WAIT, S_PLAY, S_DONE };

/* Whether path changed since the last look that came well after its change.
 * ponytail: only once it changes (o32 stat, st_mtime word 16), not a scan every poll; a scan in the
 * second of a change is not trusted, as a file could land later in that second. */
static int changed(const char *path) {
    unsigned st[36];
    if (__xstat(3, path, st)) return 0;
    if ((long)st[16] == sm.seen) return 0;
    if ((long)st[16] < time((void *)0) - 1) sm.seen = (long)st[16];
    return 1;
}

/* A file in dir named after last ("": any), not a hidden one or a download's .part or .tmp; whether
 * it is a song, stock's listing says (app_album_songs). */
static int stream_newer(const char *dir, const char *last) {
    if (!changed(dir)) return 0;
    void *d = opendir(dir);
    int found = 0;
    for (struct dirent *e; d && !found && (e = readdir(d));) {
        const char *n = e->d_name, *dot = strrchr(n, '.');
        found = n[0] != '.' && !(dot && (!strcasecmp(dot, ".part") || !strcasecmp(dot, ".tmp"))) &&
                strcmp(n, last) > 0;
    }
    if (d) closedir(d);
    return found;
}

/* Whether path has a ".." segment: all of it, or one between slashes or at either end. A name with
 * an ellipsis ("Hello... (Live).flac") has none. */
static int dotdot(const char *path) {
    for (const char *s = path;; ++s) {
        if (s[0] == '.' && s[1] == '.' && (!s[2] || s[2] == '/')) return 1;
        if (!(s = strstr(s, "/"))) return 0;
    }
}
/* A path an app or its server names: on the card, with no ".." segment. */
int card_path(const char *path) { return tk_str_start_with(path, CARD "/") && !dotdot(path); }

/* The playlist's songs from entry sm.next on, in its order, a deque the caller destroys: up to the
 * first one not there yet (sm.want), its folders' listings M3U_BATCH at most (each song's folder
 * is listed unless the song before it was in the same one). An entry off the card, or there but
 * not a song in its folder's listing, is passed over, as is one too long to be a path here; one
 * missing from a listing taken for an earlier entry may have landed since, so the next poll lists
 * its folder again before it is passed over. Entries are counted, so the app may rewrite the lines
 * from sm.next on (the one waited for, say). A file longer than M3U_MAX is read that far, its cut
 * last line dropped, so it is never waited for.
 * ponytail: the file is read whole each time it changes or a waited song lands; fine to M3U_MAX. */
static void *m3u_songs(void) {
    void *out = _create_deque("stSongInfo"), *songs = 0;
    /* state_read's leading newline and nul, and a byte past M3U_MAX that says the file goes on */
    char *raw = calloc(1, M3U_MAX + 3), dir[sizeof sm.want] = "", *line, *end;
    deque_init(out);
    if (raw && state_read(sm.dir, raw, M3U_MAX + 3) > M3U_MAX) strrchr(raw, '\n')[1] = 0;
    sm.want[0] = 0, sm.more = 0; /* at its end, unless a missing song or a full batch stops it */
    int entry = 0, lists = 0;
    for (line = raw ? raw + 1 : 0; line && *line; line = end) {
        if ((end = strstr(line, "\n"))) *end++ = 0;
        if (tk_str_start_with(line, "\xEF\xBB\xBF")) line += 3; /* a BOM */
        unsigned k = strlen(line);
        if (k && line[k - 1] == '\r') line[--k] = 0;
        if (!k || line[0] == '#' || entry++ < sm.next) continue;
        if (!card_path(line) || k >= sizeof sm.want) {
            ++sm.next;
            continue;
        }
        if (access(line, 0)) {
            tk_snprintf(sm.want, sizeof sm.want, "%s", line);
            break;
        }
        char *slash = strrchr(line, '/');
        *slash = 0;
        int fresh = !songs || strcmp(line, dir); /* its folder listed after its access() */
        if (fresh) {
            if (lists++ == M3U_BATCH) { /* this entry first on the next poll */
                sm.more = 1;
                break;
            }
            if (songs) deque_destroy(songs);
            tk_snprintf(dir, sizeof dir, "%s", line);
            songs = app_album_songs(dir);
        }
        *slash = '/';
        int found = 0;
        for (unsigned i = 0, n = deque_size(songs); !found && i < n; ++i) {
            void *r = deque_at(songs, i);
            const char *p = P(r, REC_PATH);
            if ((found = p && !strcmp(p, line))) _deque_push_back(out, r);
        }
        if (!found && !fresh) { /* landed since the listing an earlier entry took: listed again */
            sm.more = 1;
            break;
        }
        ++sm.next;
    }
    free(raw);
    if (songs) deque_destroy(songs);
    return out;
}

/* m3u mode's wake: a batch to go on with, the file rewritten, or the song waited for there. */
static int m3u_ready(void) {
    return sm.more || changed(sm.dir) || (sm.want[0] && !access(sm.want, 0));
}

/* A scan running, or its window still open: the user's own Update Local Music page
 * (updatemusic_page), whose OK starts one, or its dialog (updatemusic_dialog), which a library
 * request opens without the page and which stays up after the scan is done. */
static int scanning(void) {
    return library_scanning() || navigator_window_is_exist("updatemusic_page") ||
           navigator_window_is_exist("updatemusic_dialog");
}

static int stream_start(const void *unused) {
    (void)unused;
    sm.timer = 0;
    if (sm.m3u && playback_loads != sm.loads) sm.state = S_DONE; /* the user played a queue since */
    if (sm.state != S_WAIT) return 0;
    if (scanning()) { /* no listing during a scan, no play over its dialog: looked at again after */
        sm.seen = 0;
        return 0;
    }
    void *songs = sm.m3u ? m3u_songs() : app_album_songs(sm.dir);
    unsigned n = deque_size(songs);
    if (n) {
        if (sm.m3u)
            tk_snprintf(sm.first, sizeof sm.first, "%s",
                        (const char *)P(deque_at(songs, 0), REC_PATH));
        else
            tk_snprintf(sm.last, sizeof sm.last, "%s",
                        (const char *)P(deque_at(songs, n - 1), REC_NAME));
        sm.state = S_PLAY;
        press_natural = 1;             /* the app's .play, from its poll: nobody here pressed */
        play_folder(songs, 0, 0xf001); /* playing_page copies the queue at once */
        press_natural = 0;
        sm.loads = playback_loads;     /* its load, which play_folder has done (playback_groups) */
    }
    deque_destroy(songs);
    return 0;
}

/* A URL queue's start, on a timer as stream_start's (a window opens); then the session is done:
 * the queue is stock's, and the app reads nothing back. */
static int urls_start(const void *unused) {
    (void)unused;
    sm.timer = 0;
    if (playback_loads != sm.loads) sm.state = S_DONE; /* the user played a queue since */
    if (sm.state != S_WAIT || scanning()) return 0;    /* looked at again after the scan */
    press_natural = 1;
    app_urls_play(sm.dir);
    press_natural = 0;
    sm.state = S_DONE;
    return 0;
}

/* Whether the queue playing is the session's: its first song in its folder; or a playlist's first
 * song, and no queue loaded since its own (a Library play of the same album is the user's). */
static void *stream_queue(void) {
    void *q = P(mcl_pdeqplaylist, 0);
    const char *p = q && deque_size(q) ? P(deque_at(q, 0), REC_PATH) : 0;
    return p && (sm.m3u ? playback_loads == sm.loads && !strcmp(p, sm.first)
                        : tk_str_start_with(p, sm.dir) && p[strlen(sm.dir)] == '/')
               ? q
               : 0;
}

static void stream_poll(app_t *a) {
    char path[48], raw[192];
    tk_snprintf(path, sizeof path, "/tmp/q2-%s.play", a->id);
    unsigned got = state_read(path, raw, sizeof raw);
    if (!got) return;
    unsigned h = hash_bytes(FNV_SEED, (const unsigned char *)raw, strlen(raw));
    /* a new session: this app's .play changed. Another app's, left in /tmp while it runs, never
     * takes the queue back. */
    if (h != a->play) {
        char *dir = strstr(raw, " ");
        a->play = h, sm.app = a;
        for (char *c = dir; c && *c; ++c)
            if (*c == '\n' || *c == '\r') *c = 0;
        tk_snprintf(sm.dir, sizeof sm.dir, "%s", dir ? dir + 1 : "");
        sm.last[0] = sm.playing[0] = 0;
        sm.seen = 0;
        unsigned k = strlen(sm.dir);
        sm.m3u = k > 4 && !strcasecmp(sm.dir + k - 4, ".m3u");
        char own[40];
        tk_snprintf(own, sizeof own, "/tmp/q2-%s.urls", a->id);
        sm.urls = !strcmp(sm.dir, own); /* the app's own alone, in /tmp */
        sm.next = sm.more = 0, sm.first[0] = sm.want[0] = 0;
        sm.loads = playback_loads;
        /* a playlist on the card, or a hidden folder of it, as the app's own are, or its URL queue;
         * whole in raw and in sm.dir, as a cut path names another */
        sm.state = got < sizeof raw - 2 && dir && !dir[1 + k] && !dotdot(sm.dir) &&
                           (sm.urls || tk_str_start_with(sm.dir, sm.m3u ? CARD "/" : CARD "/."))
                       ? S_WAIT
                       : S_DONE;
        stop_timer(&sm.timer);
    }
    if (sm.app != a) return;
    /* A song's folder listing (toolsLoadDirectory) refills tools_pdeq_directory, which a library
     * scan's thread fills too: none while one runs, the scan dialog's own loop included; and
     * play_folder would open Now Playing over the dialog, so nothing until it closes. */
    int scan = scanning();
    if (sm.state == S_WAIT && sm.urls) { /* played whole at once, by stock's Tidal player */
        if (playback_loads != sm.loads)
            sm.state = S_DONE; /* the user played a queue before it started */
        else if (!sm.timer && !scan && !addon_game_audio()) /* not under a card game's sound */
            sm.timer = timer_add(urls_start, 0, 0);
        return;
    }
    if (sm.state == S_WAIT) {
        if (sm.m3u && playback_loads != sm.loads)
            sm.state = S_DONE; /* the user played a queue before its first song came */
        else if (!sm.timer && !scan && !addon_game_audio() && /* the game's sound: the song waits */
                 (sm.m3u ? m3u_ready() : stream_newer(sm.dir, "")))
            sm.timer = timer_add(stream_start, 0, 0);
        return;
    }
    void *q = sm.state == S_PLAY ? stream_queue() : 0;
    if (!q) {
        sm.state = S_DONE; /* something else plays now: the session is left */
        return;
    }
    if (!scan && sm.m3u) {
        if (m3u_ready()) {
            void *add = m3u_songs();
            if (deque_size(add)) playback_queue_insert(q, add, 0);
            deque_destroy(add);
        }
    } else if (!scan && stream_newer(sm.dir, sm.last)) {
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
    /* a path sm.playing can't hold is no use to the app cut, and tk_snprintf's count would pass
     * line's end */
    if (now && strlen(now) < sizeof sm.playing && strcmp(now, sm.playing)) {
        char line[sizeof sm.playing + 1];
        int n = tk_snprintf(line, sizeof line, "%s\n", now);
        tk_snprintf(sm.playing, sizeof sm.playing, "%s", now);
        tk_snprintf(path, sizeof path, "/tmp/q2-%s.playing", a->id);
        blob_io(path, "/tmp/q2-stream.tmp", line, (unsigned)n, 1);
    }
}

/* Sleep timers (q2-podcasts' Sleep Timer): an app's /tmp/q2-<id>.sleep holds "until <time>"
 * (seconds since the epoch, at most SLEEP_MAX_S away) or "end" (the track playing now ends). Q2 Pod
 * keeps the timer itself, so it pauses whatever plays (media_set_playing) once the app has stopped
 * too. A time is kept as the time left on the boot clock (boot_s), which counts Standby and doesn't
 * move when Wi-Fi sets the clock; one the Q2 slept through by more than SLEEP_LATE_S is let go.
 * "end" pauses when the track ends by itself (playback.c auto_nexts) or leaves its last SLEEP_END_S
 * seconds for the next one, which then waits at its start, or starts over (repeat one); another
 * track picked, or a seek back, waits for that one's end instead (stock refills the same queue, so
 * a new queue looks the same), and playback stopped for SLEEP_STOP_MS (not between tracks) or
 * another player lets it go. Done or let go, the file is removed: the app's sign that no timer
 * runs. The app removes it to cancel. One timer: the latest asked, any app's. */
#define SLEEP_MAX_S (7 * 24 * 3600)
#define SLEEP_LATE_S 60
#define SLEEP_END_S 2
#define SLEEP_STOP_MS 3000
static struct {
    unsigned seen[APPS_MAX]; /* each app's .sleep as read last, hashed; 0 none */
    long at;                 /* boot_s() to pause at; 0 none */
    int end, owner;          /* end: pause when the local track ends; owner: the app's slot + 1 */
    unsigned pos, nexts;     /* the track "end" waits on, by its place in the queue; auto_nexts */
    int sec, total;          /* its elapsed and length as last seen, seconds; total 0 unknown */
    unsigned stopped;        /* time_now_ms() playback was seen stopped since; 0 not stopped */
} sl __attribute__((section(".scratch")));

static void sleep_path(char *out, unsigned size, int slot) {
    tk_snprintf(out, size, "/tmp/q2-%s.sleep", ap.a[slot].id);
}

static void sleep_done(void) {
    char path[48];
    if (sl.owner)
        sleep_path(path, sizeof path, sl.owner - 1), unlink(path), sl.seen[sl.owner - 1] = 0;
    sl.at = 0, sl.end = 0, sl.owner = 0;
}

/* Seconds since boot, Standby included. */
static long boot_s(void) {
    struct {
        long s, ns;
    } t = { 0 };                  /* struct timespec */
    clock_gettime(7, (void *)&t); /* CLOCK_BOOTTIME */
    return t.s;
}

/* An app's .sleep, read while it runs: a new request replaces the timer, its removal cancels it. */
static void sleep_read(app_t *a) {
    char path[48], raw[48];
    int slot = (int)(a - ap.a);
    sleep_path(path, sizeof path, slot);
    int have = state_read(path, raw, sizeof raw);
    unsigned h = have ? hash_bytes(FNV_SEED, (const unsigned char *)raw, strlen(raw)) | 1 : 0;
    if (h == sl.seen[slot]) return;
    sl.seen[slot] = h;
    if (!have) { /* the app cancelled its own */
        if (sl.owner == slot + 1) sl.at = 0, sl.end = 0, sl.owner = 0;
        return;
    }
    if (sl.owner != slot + 1) sleep_done(); /* another app's timer gives way, its file removed */
    sl.owner = slot + 1, sl.at = 0, sl.end = 0; /* and this app's earlier one */
    const char *req = raw + 1;                  /* state_read puts a newline first, for field() */
    if (tk_str_start_with(req, "end")) {
        sl.end =
            media_owner() == 1 && mclGetPlayStatus() != 1; /* the local player, playing or paused */
        sl.pos = (unsigned)I(MCL_POS, 0), sl.nexts = auto_nexts, sl.sec = -1, sl.total = 0;
        sl.stopped = 0;
        if (!sl.end) sleep_done();
    } else if (tk_str_start_with(req, "until ")) {
        long long at = 0, now = time((void *)0);
        const char *c = req + 6;
        for (; *c >= '0' && *c <= '9' && c < req + 6 + 11; ++c) at = at * 10 + (*c - '0');
        if (c == req + 6 || (*c && *c != '\n') || at - now > SLEEP_MAX_S) {
            sleep_done(); /* no time, or none this app could mean: dropped, nothing paused */
            return;
        }
        if (at <= now) { /* due; past by SLEEP_LATE_S (a stale file): let go, as sleep_check does */
            if (now - at < SLEEP_LATE_S) media_set_playing(0);
            sleep_done();
            return;
        }
        sl.at = boot_s() + (long)(at - now);
    } else
        sleep_done();
}

/* Every UI pass: a timer due, or the track "end" waited for over, pauses. */
static void sleep_check(void) {
    if (sl.at) {
        long late = boot_s() - sl.at;
        if (late >= SLEEP_LATE_S)
            sleep_done(); /* the Q2 slept through it */
        else if (late >= 0)
            media_set_playing(0), sleep_done();
    } else if (sl.end) {
        unsigned now = time_now_ms(), pos = (unsigned)I(MCL_POS, 0);
        if (mclGetPlayStatus() == 1) { /* stopped, or between tracks for a moment */
            if (!sl.stopped) sl.stopped = now | 1;
            if ((int)(now - sl.stopped) >= SLEEP_STOP_MS) sleep_done(); /* | 1: maybe now + 1 */
            return;
        }
        sl.stopped = 0;
        if (media_owner() != 1) {
            sleep_done(); /* another player: nothing to wait for */
            return;
        }
        int sec = -1, total = 0;
        player_playtime_and_length(&sec, &total);
        if (sec < 0) return; /* between tracks: a later pass decides */
        /* ended by itself, or at its end when last seen; a length unknown, any move on counts */
        int ended = auto_nexts != sl.nexts ||
                    (sl.sec >= 0 && (sl.total <= 0 || sl.sec >= sl.total - SLEEP_END_S));
        if (pos != sl.pos || sec + SLEEP_END_S < sl.sec) { /* moved on, or back */
            if (ended) {
                media_set_playing(0), sleep_done();
                return;
            }
            sl.pos = pos, sl.nexts = auto_nexts; /* another track picked, or a seek back */
        }
        sl.sec = sec, sl.total = total;
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
    /* hold= may come anywhere, after the long qr= too. ponytail: only the first QR_RAW - 2 bytes
     * (1,534) are read, so a hold= further on reads as 0; the largest code the QR page draws
     * (49 modules, QR_HEX) takes about 640. A longer file needs a larger buffer here. */
    char raw[QR_RAW];
    state_read(a->state, raw, sizeof raw);
    a->hold = number(raw, "hold") == 1;
    if (a->hold) reset_poweroptions_timer(1, 1, 0); /* standby, auto power-off; not the screen's */
    stream_poll(a);
    sleep_read(a);
    if (access(a->pid, 0) && now - a->launch_at > ADDON_START_MS) a->launched = a->hold = 0;
}

/* Card games: Library's Games lists them after its own (games.c); a row runs the game's binary
 * from its folder, with no launcher, over the whole screen while demo goes on without painting
 * (navigation.c ringnav_wm_paint) or input (ringnav_input), as Videos' q2video does. Each input
 * event reaches the game instead as a text datagram on /tmp/q2-<id>.input.sock:
 *   "t d X Y", "t m X Y", "t u X Y"   touch down, move, up (an abort too), in the landscape
 *                                     view's pixels (375 x 320), turned from the panel's
 *   "k d CODE", "k u CODE"            a key down, up (AWTK's codes: Return 170, Centre 218)
 *   "w N"                             the wheel's raw travel since the last (wheel_raw: 200 a turn,
 *                                     rising anticlockwise), sent once a UI loop pass
 * Play/Pause and the side buttons stay the music's, as stock has them under the Games page, unless
 * the game's q2app says audio=1: the sound is then the game's (player_stop and dac_take before it
 * starts, as video_start; the output device and, on Bluetooth or USB, the volume as its arguments,
 * as q2video gets them, and opened as q2video opens them, video.c sound_open; g_dacoff_time held
 * while it runs), those keys reach it as "k d|u 171", 222 and 223, a headset's buttons do nothing
 * (headset.c media_key, media_set_playing), and the idle quit below is left to the game, which
 * alone knows whether it is playing. Music stays stopped when it ends, as after a video. The game
 * ends itself (from its Return menu); Return held GAME_HOLD_MS ends it (TERM, and its release is
 * dropped so stock's Games stays), as does GAME_IDLE_MS without input unless Smart Awake is on;
 * one still there GAME_KILL_MS after a TERM is KILLed. demo repaints when it has gone. Stock makes
 * a held key's long press after this hook, so the hold is timed here, from the game's key-down.
 * ponytail: the screen stays on while it runs, as for a video, and the idle quit is what lets it
 * sleep without Smart Awake; a game that wants to sleep on screen needs the wake path to reach
 * stock under it. */
#define GAME_IDLE_MS 120000
#define GAME_HOLD_MS 700 /* navigation.c's KEY_HOLD_MS, Return's hold elsewhere */
#define GAME_KILL_MS 3000
static struct {
    int pid, sock, eat;  /* eat: the held Return's release, after the game it ended */
    int audio;           /* the running game's audio=1: the keys and the DAC are its */
    unsigned seen;       /* time_now_ms of the last input */
    unsigned held, term; /* when Return went down, when TERM went (0: neither) */
    char path[48];       /* the game's socket */
} gm __attribute__((section(".scratch")));

int addon_games(void) { return addon_scan(), ap.games; }
int addon_games_last(void) { return ap.games; }
const char *addon_game_name(int k) { return k >= 0 && k < ap.games ? ap.a[ap.game[k]].name : 0; }
int addon_game_on(void) { return gm.pid; }
int addon_game_audio(void) { return gm.pid && gm.audio; }

static void game_kill(const char *how) {
    char cmd[24];
    tk_snprintf(cmd, sizeof cmd, "kill %s%d", how, gm.pid); /* demo imports no kill() */
    system(cmd);
}
static void game_end(void) {
    if (gm.term) return; /* game_poll KILLs it GAME_KILL_MS on */
    gm.term = (unsigned)time_now_ms() | 1;
    game_kill("");
}

/* netupdate.c's toast once app id is installed: a game is in Games, not Streaming */
int addon_is_game(const char *id) {
    addon_scan();
    for (int k = 0; k < ap.games; ++k)
        if (!strcmp(ap.a[ap.game[k]].id, id)) return 1;
    return 0;
}

/* Home's Shortcut (theme_engine.c home_shortcut_open): the card game with this id started as its
 * Games row would start it (1 running, 0 not, with addon_game_start's toast); -1 when no card game
 * has the id (no q2app with game=1 in its folder, or no slot left). */
int addon_game_open(const char *id) {
    int n = addon_games();
    for (int k = 0; k < n; ++k)
        if (!strcmp(ap.a[ap.game[k]].id, id)) return addon_game_start(k);
    return -1;
}

/* Card game k started over the screen: 1 when it runs. */
int addon_game_start(int k) {
    app_t *a = k >= 0 && k < ap.games ? &ap.a[ap.game[k]] : 0;
    if (gm.pid || !a) return 0;
    const char *busy = shelf_busy();
    if (busy && !strcmp(busy + 1, a->id)) return toast("Wait until the App Store finishes"), 0;
    if (access(a->bin, 0)) return toast("It isn't on the card"), 0;
    char vol[5];
    const char *dev = 0;
    if (a->audio) { /* the sound is its: hciplayer off the PCM, the jack readied, as video_play */
        headset_manual();
        spot_yield();
        radio_stop();
        player_stop();
        dev = output_device(vol);
        if (!dev) dac_take();
    }
    int pid = fork();
    if (!pid) {
        child_oom_first();
        for (int fd = 3; fd < 1024; ++fd) close(fd); /* demo's: the watchdog, the screen, sockets */
        if (a->audio) /* q2video's arguments: the device, then a soft output's volume */
            execl(a->bin, a->bin, dev ? dev : "plughw:0,0", dev ? vol : (char *)0, (char *)0);
        else
            execl(a->bin, a->bin, (char *)0);
        child_exit(127);
    }
    if (pid < 0) return toast("It couldn't start"), 0;
    gm.pid = pid;
    gm.audio = a->audio;
    gm.sock = socket(1, 1, 0); /* AF_UNIX, SOCK_DGRAM (MIPS numbering) */
    gm.seen = time_now_ms();
    gm.held = gm.term = gm.eat = 0;
    tk_snprintf(gm.path, sizeof gm.path, "/tmp/q2-%s.input.sock", a->id);
    __sync_lock_test_and_set(&wheel_travel, 0);
    wheel_raw = 1;
    return 1;
}

/* ringnav_input, every event: 1 when it is the game's (sent as its datagram) or the release of the
 * Return held to end it; 0 for stock's, a music key among them */
int addon_game_input(void *e) {
    if (!e) return gm.pid != 0;
    unsigned type = (unsigned)I(e, EVENT_TYPE), key = (unsigned)I(e, EVENT_KEY);
    int keyish = type == EVT_KEY_DOWN || type == EVT_KEY_UP || type == EVT_KEY_LONG;
    if (keyish && key == KEY_RETURN && gm.eat) /* the held Return's repeats and release */
        return gm.eat = type != EVT_KEY_UP, 1;
    if (!gm.pid) return 0;
    if (!gm.audio && keyish && (key == KEY_PLAY || key == KEY_BACK_BTN || key == KEY_FWD_BTN))
        return 0;
    if (keyish && key == KEY_RETURN)
        gm.held = type == EVT_KEY_UP ? 0
                  : type == EVT_KEY_DOWN && !gm.held
                      ? (unsigned)time_now_ms() | 1 /* not a repeat's */
                      : gm.held;
    char m[32];
    m[0] = 0;
    if (type == EVT_KEY_DOWN || type == EVT_KEY_UP)
        tk_snprintf(m, sizeof m, "k %s %u", type == EVT_KEY_UP ? "u" : "d", key);
    else if ((type >= EVT_POINTER_DOWN - 1 && type <= EVT_POINTER_UP_BEFORE) ||
             type == EVT_POINTER_ABORT) {
        const char *what = type == EVT_POINTER_ABORT || type >= EVT_POINTER_UP_BEFORE - 1 ? "u"
                           : type >= EVT_POINTER_MOVE_BEFORE - 1                          ? "m"
                                                                                          : "d";
        /* here, before the window manager turns it, a touch is the portrait panel's: the view
         * (375 x 320) is the panel turned anticlockwise, so its x is the panel's y */
        tk_snprintf(m, sizeof m, "t %s %d %d", what, I(e, EVENT_Y), 319 - I(e, EVENT_X));
    }
    gm.seen = time_now_ms();
    if (m[0]) dgram_send(&gm.sock, gm.path, m, strlen(m));
    return 1;
}

/* every UI loop pass: the wheel's travel, the screen and standby held, the idle quit, its end */
static void game_poll(void) {
    int status = 0;
    if (!gm.pid) return;
    if (waitpid(gm.pid, &status, 1)) { /* WNOHANG: it has ended */
        close(gm.sock);
        /* a jack or USB change meanwhile ran stock's player_save_start(1): its start failed
         * (ringnav_start_player) but its pause stuck on nothing, so Play would do nothing */
        if (gm.audio && mclGetPlayStatus() == 3) player_stop();
        gm.pid = gm.audio = 0;
        wheel_raw = 0;
        widget_invalidate_force(window_manager(), 0);
        if (status == 127 << 8) toast("It couldn't start"); /* the child's execl failed */
        return;
    }
    unsigned now = (unsigned)time_now_ms();
    /* signed: a stamp can be just after now (| 1 rounds up, game_end reads the clock again) */
    if (gm.held && (int)(now - gm.held) >= GAME_HOLD_MS) gm.held = 0, gm.eat = 1, game_end();
    if (gm.term && (int)(now - gm.term) >= GAME_KILL_MS) gm.term = now | 1, game_kill("-9 ");
    int travel = __sync_lock_test_and_set(&wheel_travel, 0);
    if (travel) {
        char m[16];
        tk_snprintf(m, sizeof m, "w %d", travel);
        dgram_send(&gm.sock, gm.path, m, strlen(m));
        gm.seen = time_now_ms();
    }
    reset_poweroptions_timer(1, 1, 1);
    if (gm.audio) { /* the DAC kept on, as video_poll; the idle quit is the game's own */
        if (I(g_dacoff_time, 0) > 0) I(g_dacoff_time, 0) = 0;
        return;
    }
    if (time_now_ms() - gm.seen < GAME_IDLE_MS) return;
    if (!ringnav_smart_awake()) game_end();
    gm.seen = time_now_ms(); /* Smart Awake: asked again GAME_IDLE_MS on */
}

/* Smart Wi-Fi (smart_wifi.c smart_needed): an app started, or restarting after Retry, keeps the radio
 * on until it stops (by itself, Stop Streaming Services or Rockbox). One stopped for USB storage
 * counts once it starts again: the cable holds the radio meanwhile, and an app deleted from the
 * computer, which never starts again, holds nothing. */
int addon_running(void) {
    for (int i = 0; i < ap.n; ++i)
        if (ap.a[i].launched || ap.a[i].restart) return 1;
    return 0;
}

void addon_poll(void) {
    game_poll();
    for (int i = 0; i < ap.n; ++i) poll_one(&ap.a[i]);
    sleep_check();
    library_poll();
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
 * (addon_poll). On the UI thread, so it must stay short; addon_quit is the Apps page's worker's.
 * ponytail: an app still saving on its TERM after AWAY_MS can write to the card as USB takes it;
 * the real fix defers stock's USB-mode call until the apps are gone, without sleeping. */
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
 * one, asked to end and waited for on the Apps page's worker, so its files are closed before they
 * move (up to QUIT_MS: an app still saving on its TERM after 3 s, a login, would write it back
 * into a folder just removed); then, once they moved or went, forgotten on the UI thread, so its row starts the new one
 * (or none). An update that failed or was cancelled before it stopped the app leaves it running and tracked. */
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
    for (unsigned long long end = now_ms() + QUIT_MS; !access(pid, 0) && now_ms() < end;)
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
 * before is dropped. When it can't start, req.done is set at once, so the tick reports it. Once
 * the thread runs, done is the worker's alone: it may have finished already. */
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
    if (!req.running) req.done = 1;
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
 * reply's message, search opens the T9 box (OK opens "search" for the text; a list's first one is
 * the title bar's search icon instead, take_find), sort opens as list does (caps word "sort"; one
 * just after a taken search is the title bar's sort icon instead, take_sort), remote opens the QR
 * code, local plays from the card (uri: a song, or a folder to list; context "album": in track
 * order), info does nothing; its position, when set, is the op a held Play/Pause sends. progress
 * (progress_row) draws a bar of its position, permille, under its title and subtitle; a press or a
 * held Play/Pause does nothing. page (caps word "window") shows the level's own list from the
 * cursor in its context, in the level's place rather than over it (page_to): uri "prev" slides it
 * in from the left and selects its last row, any other from the right, selecting its row after the
 * first (the app's Previous page row).
 * A reply whose message is "refresh" is asked again every ADDON_LIVE_MS while its level is on top,
 * and at once after an action on it (the level stays shown, no Loading…), and redrawn only when it
 * changed, its selected row kept; one with the same rows (same_rows) is written in place (relabel)
 * instead of rebuilt. One whose message is "pages" and that has a cursor
 * is followed by its next pages (page_next), appended to its list (append) under a Loading… row;
 * any other keeps More….
 * The first level's reply whose message is "sections" names the app's sections instead (sec_take):
 * each list row one, its uri the op that fills it, its title the page's, its context the op's
 * argument. The page shows one section at a time as its first level, the first to begin with, and
 * a sideways swipe on it (swipe) opens the next or the previous, its list sliding in from that
 * side; none past the ends. Only the first level swipes: a level opened from a section is a level
 * as any other, and Back returns to the section. The wheel never changes section. */
enum { K_LOAD, K_READY, K_ERROR, K_WAIT }; /* K_WAIT: no socket yet, the app starting */
enum { G_ROW, G_SHOW, G_SEARCH, G_SECTION, G_FIND, G_SORT };

/* A level's page that can't show the app's rows (an error, or a load or start taking SLOW_MS) has a
 * Stop row whenever the app may be running, so a frozen one can always be ended; unless its q2app
 * says stop=0, when the error page's second row is Cancel (Return's) and a slow load has none. */
typedef struct {
    int state, n, sel, live, retry;
    int nosock, slow;  /* nosock: the error is no socket; slow: the wait's Stop row shown */
    int art;           /* its rows are drawn with covers (art_row): how many have one, 0 none */
    int find;          /* its first row, a search, is the title bar's search icon (take_find) */
    int sort;          /* and its next, a sort, the sort icon left of it (take_sort) */
    unsigned since;    /* when it began loading or waiting, 0 once done */
    unsigned base;     /* args' bytes before a More…'s cursor: the list's own */
    unsigned id, hash; /* id: the fetch whose reply this level takes; hash: its rows' bytes */
    char title[128], note[128], op[16], args[400]; /* args: "\t" and escaped each */
} lv_t;

static struct {
    void *page, *view, *edit;
    unsigned timer, go, leave, again; /* again: when a waiting or live level asks again */
    int depth, act, next, arg, a,
        code; /* act: what the request in flight is for, 1 an action; a: the app's slot; code: its
                 QR code to show at the first answer */
    int arow, busy; /* the action's row, and whether it says Working… (busy_row) */
    unsigned acted; /* when the action was sent */
    char term[64], aop[16];
    lv_t *lv; /* ADDON_DEPTH of them, calloc'd */
    /* The shown reply, the top level's (no other keeps one): pages replies, each realloc'd to its
     * size, their fields in f (calloc'd with lv), every page's rows in turn; paging: a next page in
     * flight (page_next). */
    char *buf[ADDON_PAGES], **f;
    int pages, paging;
    signed char moved; /* the depth's change the next rows or error push in: 1 deeper, -1 back */
    lv_t *sec; /* the first page's sections, each the level it opens, calloc'd; nsec of them */
    signed char nsec, cur; /* cur: the one showing */
    /* a touch on the page: where it went down, and whether a sideways swipe has taken it (swipe) */
    int sx, sy;
    char sdown, sclaim;
    int art_lo, art_hi; /* the rows whose covers may be decoded, half open (art_window) */
    char *sort[6];      /* the sort icon's row (take_sort), its fields in the shown reply */
} pg __attribute__((section(".scratch")));

static void qr_open(const char *state, const char *title);
static app_t *app(void) { return &ap.a[pg.a]; }
static lv_t *top(void) { return &pg.lv[pg.depth - 1]; }
static const char *cell(int i, int k) { return pg.f[4 + 6 * i + k]; }

/* "\t" and s escaped appended to out (size bytes); 0 when it doesn't fit. */
static int put(char *out, unsigned size, const char *s) {
    unsigned k = strlen(out);
    return k + 1 < size && (out[k] = '\t', spot_escape(s, out + k + 1, size - k - 1) >= 0);
}

static void fetch(lv_t *l) {
    pg.act = 0;
    pg.paging = 0;
    req_send(app()->browse, l->op, l->args);
    l->id = req.id;
}

/* Row i's action op sent with its context, the one in flight dropped (its row's own text back).
 * go() sends none while one is in flight: the app has usually done it, and a second would drop its
 * reply, so a toggle pressed twice undid itself. */
static void busy_row(int on);
static void action(int i, const char *op, const char *ctx) {
    char a[sizeof top()->args] = "";
    if (ctx[0] && !put(a, sizeof a, ctx)) return;
    busy_row(0);
    tk_snprintf(pg.aop, sizeof pg.aop, "%s", op);
    req_send(app()->browse, op, a);
    pg.act = 1, pg.arow = i, pg.busy = 0, pg.acted = time_now_ms();
}

static int clicked(void *ctx, void *event);

/* Whether row i opens a page (go()): a list (a sort row left as a row is one), the phone remote's
 * QR page or a folder on the card. Its row has a chevron. */
static int opens(int i) {
    const char *kind = cell(i, 0), *uri = cell(i, 1);
    return !strcmp(kind, "list") || !strcmp(kind, "sort") || !strcmp(kind, "remote") ||
           (!strcmp(kind, "local") && *uri && uri[strlen(uri) - 1] == '/');
}

/* Whether the app may be running, so a Stop row can end it; never for one whose q2app says stop=0,
 * whose error page ends in Cancel instead. */
static int stoppable(void) {
    return !app()->nostop && (app()->launched || app()->restart || !access(app()->pid, 0));
}

static void stop_row(int i) {
    char t[48];
    tk_snprintf(t, sizeof t, "Stop %s", app()->name);
    page_row_detail(pg.view, i, t, 0, clicked);
}

static int progress_h(void) {
    return theme()->painter[PAINT_SCRUB] == SCRUB_ROWS ? PROGRESS_GLOSS : PROGRESS_H;
}

/* A progress row's bar's permille, clamped to 0 to 1000 for addon_paint. */
static void set_pml(void *bar, long permille) {
    widget_set_prop_int(bar, "_pml", permille < 0 ? 0 : permille > 1000 ? 1000 : (int)permille);
}

/* A progress row: page_row_detail's item re-dressed, its title and subtitle in the caption's box
 * (the theme's: an empty detail has moved the label), left and right on one line, over a bar
 * centred in the detail's, which addon_paint fills from the left: its _pml prop, permille, clamped
 * to 0 to 1000 (set_pml); strtol saturates what overflows, which atoi leaves undefined. */
static void progress_row(void *item, long permille) {
    const theme_t *T = theme();
    void *t = widget_get_child(item, 0), *s = widget_get_child(item, 1);
    int x = I(t, W_X), w = I(t, W_W), h = T->page_cap_h;
    int y = T->page_cap_y + (I(item, W_H) - page_h(64)) / 2; /* a cover list's rows are taller */
    int dx = I(s, W_X), dy = I(s, W_Y), dw = I(s, W_W), dh = I(s, W_H);
    widget_move_resize(t, x, y, w * 3 / 5, h);
    widget_use_style(s, "s_scrlabel_white20r"); /* the style drops the detail's size and grey */
    widget_set_prop_int(s, "style:normal:font_size", 16);
    widget_set_prop_int(s, "style:normal:text_color", (int)SPOT_GREY);
    widget_set_prop_int(s, "_font", -1); /* and its face */
    theme_font(s, ROLE_META, FONT_ROW);
    widget_move_resize(s, x + w * 3 / 5, y, w - w * 3 / 5, h);
    int bh = progress_h();
    void *bar = widget_factory_create_widget(widget_factory(), "view", item, dx, dy + (dh - bh) / 2,
                                             dw, bh);
    set_pml(bar, permille);
}

/* Row i's cover's file:// URL written to out (ART_URL bytes); no file is touched. */
static void art_url(int i, char *out) {
    unsigned key = hash_bytes(FNV_SEED, (const unsigned char *)cell(i, 1), strlen(cell(i, 1)));
    key = hash_bytes(key, (const unsigned char *)"\t", 1);
    key = hash_bytes(key, (const unsigned char *)cell(i, 4), strlen(cell(i, 4)));
    tk_snprintf(out, ART_URL, "file:///tmp/q2-%s.art/%08x.jpg", app()->id, key);
}

/* Row i's cover, its URL in out (art_url); whether the file is there, with a known size of no more
 * than ART_MAX px either way. */
static int art_file(int i, char *out) {
    int w = 0, h = 0;
    art_url(i, out);
    return picture_size(out + 7, &w, &h) && w && h && w <= ART_MAX && h <= ART_MAX;
}

/* Rows from.. to's covers, decoded or not, let go of the image manager. */
static void art_unload(int from, int to) {
    char url[ART_URL];
    for (; from < to; ++from) {
        art_url(from, url);
        image_manager_unload_bitmap_by_name(image_manager(), url);
    }
}

/* The range (art_window) unloaded and emptied; pg.f still names its rows. */
static void art_forget(void) {
    art_unload(pg.art_lo, pg.art_hi);
    pg.art_lo = pg.art_hi = 0;
}

/* The shown reply let go, and any page it waits for; its covers leave the image manager while
 * pg.f still names their rows, whether or not the files are still there. The rows painted since
 * the last tick count too. */
static void art_window(void);
static void drop(void) {
    art_window();
    art_forget();
    while (pg.pages) free(pg.buf[--pg.pages]);
    pg.paging = 0;
}

/* The list's own arguments in a (n bytes), and cursor c as Q2SPOT/1's last, none for an empty c (a
 * page row's for the list's start); 0 when they don't fit. */
static int cursor_args(const lv_t *l, char *a, unsigned n, const char *c) {
    tk_snprintf(a, n, "%.*s", (int)l->base, l->args);
    return !c[0] || put(a, n, c);
}

/* The level's next page asked for, as More… would, its reply appended by got(). */
static void page_next(lv_t *l) {
    char a[sizeof l->args] = "";
    pg.act = 0;
    if ((pg.paging = cursor_args(l, a, sizeof a, pg.f[3])))
        req_send(app()->browse, l->op, a), l->id = req.id;
}

/* How many of the first count rows have covers, so the list is drawn with them; 0 too with a
 * progress row among them, whose bar is page_row_detail's. */
static int art_rows(int count) {
    char url[ART_URL];
    int art = 0;
    for (int i = 0; i < count; ++i) {
        if (!strcmp(cell(i, 0), "progress")) return 0;
        art += art_file(i, url);
    }
    return art;
}

static int row_h(const lv_t *l) { return l->art ? ART_ROW_H : page_h(64); }

/* A stock Tidal list row, page_row_detail's item moved over: the cover (url, or room for one) at
 * the theme's left, the theme's label boxes beside it, centred in the taller row, an empty detail
 * centring the caption, and the chevron when it opens a page. The image goes before row_into, whose
 * chevron stays the last child (navigation.c into_mark), and is sized after it. */
static void *art_row(int index, const char *caption, const char *detail, const char *url,
                     int into) {
    int x = page_x(), tx = x + ART_PX + 12, w = 375 - x - tx, dy = (ART_ROW_H - page_h(64)) / 2;
    void *item = page_row_detail(pg.view, index, caption, detail, clicked), *img = 0;
    void *t = widget_get_child(item, 0), *s = widget_get_child(item, 1);
    widget_move_resize(item, 0, index * ART_ROW_H, 375, ART_ROW_H);
    widget_move_resize(t, tx, *detail ? I(t, W_Y) + dy : 0, w, *detail ? I(t, W_H) : ART_ROW_H);
    widget_move_resize(s, tx, I(s, W_Y) + dy, w, I(s, W_H));
    if (url) {
        img = image_create(item, x, (ART_ROW_H - ART_PX) / 2, ART_PX, ART_PX);
        image_set_draw_type(img, 4); /* scaled to fit, as Cover Flow's covers */
        image_manager_unload_bitmap_by_name(image_manager(), url); /* a file written since */
        image_base_set_image(img, url);
    }
    if (into) row_into(item, ART_ROW_H);
    if (img) widget_move_resize(img, x, (ART_ROW_H - ART_PX) / 2, ART_PX, ART_PX);
    return item;
}

/* Row i of the level's list; i == l->n: the trailing row, Loading… while a page is on its way (a
 * press does nothing), else More…. */
static void row(lv_t *l, int i) {
    if (i == l->n) {
        if (l->art)
            art_row(i, pg.paging ? "Loading…" : "More…", "", 0, 0);
        else
            page_row_detail(pg.view, i, pg.paging ? "Loading…" : "More…", "", clicked);
        return;
    }
    void *item;
    if (l->art) {
        char url[ART_URL];
        item = art_row(i, cell(i, 2), cell(i, 3), art_file(i, url) ? url : 0, opens(i));
    } else
        item = (opens(i) ? page_row_detail_into : page_row_detail)(pg.view, i, cell(i, 2),
                                                                   cell(i, 3), clicked);
    if (!strcmp(cell(i, 0), "progress")) progress_row(item, strtol(cell(i, 5), 0, 10));
}

/* Stock never lets a row's decoded cover go (nothing calls image_manager_unload_unused), so one a
 * user scrolled past would stay until the list closes: at most ART_KEEP rows' stay decoded around
 * the list's real scroll position (its yoffset, so wheel, touch and fling alike), others unloaded
 * here, from show(), drop() and the tick; [pg.art_lo, pg.art_hi) names the rows that may be decoded,
 * first widened to all a move crossed since the last tick, so none is left behind, and the cost is
 * the rows crossing, not the list's length. An image draws by name and keeps no bitmap between
 * paints (internals.md Covers), so a row scrolled back loads again and nothing dangles.
 * ponytail: at most ART_KEEP covers (about 1.5 MB at ART_MAX px) plus what a fling paints in one
 * 250 ms tick; a cover scrolled back decodes again on the UI thread, a decode thread if a long
 * fling ever shows its fill-in. */
static void art_window(void) {
    const lv_t *l = top();
    if (!pg.view || !l->art || l->state != K_READY) return;
    int rh = row_h(l), y = widget_get_prop_int(pg.view, "yoffset", 0);
    if (y < 0) y = 0; /* a drag past the top: stock's rubber band reads to minus the view's height */
    /* a row either side of the view, which stock paints when it's within 4 px of the edge */
    int first = y / rh ? y / rh - 1 : 0, last = (y + I(pg.view, W_H) + rh - 1) / rh + 1;
    if (last > l->n) last = l->n;
    if (first > last) first = last;
    int lo = (first + last - ART_KEEP) / 2;
    if (lo > l->n - ART_KEEP) lo = l->n - ART_KEEP;
    if (lo < 0) lo = 0;
    int hi = lo + ART_KEEP < l->n ? lo + ART_KEEP : l->n;
    int a = pg.art_lo < pg.art_hi && pg.art_lo < first ? pg.art_lo : first;
    int b = pg.art_lo < pg.art_hi && pg.art_hi > last ? pg.art_hi : last;
    art_unload(a, b < lo ? b : lo);
    art_unload(a > hi ? a : hi, b);
    pg.art_lo = a > lo ? a : lo;
    pg.art_hi = b < hi ? b : hi;
}

static void show(void);

/* The first of reply f's *n rows out, the rest up one, if it is of this kind and not the sole row. */
static int take_row(char **f, int *n, const char *kind) {
    if (*n < 2 || strcmp(f[4], kind)) return 0;
    for (int i = 4; i < 4 + 6 * (*n - 1); ++i) f[i] = f[i + 6];
    --*n;
    return 1;
}

/* A first row of kind search leaves the list for a search icon at the title bar's right, as
 * Tidal's navbar has (s_img_search), where the theme has a title bar; under one without (the name
 * in the status bar) the row stays, as does a sole row, which would leave an empty list. The row's
 * title and subtitle aren't shown: the box's tips are the app's. Whether it did, from reply f's *n
 * rows. */
static int take_find(char **f, int *n) {
    return theme()->page_title_h && take_row(f, n, "search");
}

/* After a taken search, a first row of kind sort leaves the list too, for a sort icon left of the
 * search icon, its fields kept (pg.sort) for the tap, which opens it as a list row's press does.
 * A sole row stays, as a sole search. Whether it did, from reply f's *n rows. */
static int take_sort(char **f, int *n) {
    memcpy(pg.sort, f + 4, sizeof pg.sort); /* read only where l->sort says it was taken */
    return take_row(f, n, "sort");
}

static int bar_tapped(void *ctx, void *event);

/* A 50 px image at x in the title bar, h high from y, in stock style s: clickable (an image sends
 * no click unless it is, as Tidal's img_search), a tap queueing code. */
static void bar_icon(int x, int y, int h, const char *s, int code) {
    void *icon = image_create(pg.page, x, y, 50, h);
    widget_use_style(icon, s);
    widget_set_prop_int(icon, "clickable", 1);
    widget_on(icon, EVT_CLICK, bar_tapped, (void *)(long)code);
}

/* The search icon at the title bar's right, and the sort icon left of it when the level has one,
 * both in the same place on every such page. The title stays centred on the screen, in a box that
 * ends where the first icon's ink starts (the glass's box alone, 73 to 302, as before), so a longer
 * one scrolls clear of them.
 * ponytail: taps only, so the wheel and buttons don't reach search or sort on such a list; a key
 * bound to G_FIND or G_SORT if that is ever asked for. */
#define FIND_X 302 /* the rows' list_into chevron's box (row_into), so the icon lines up with them */
/* The sort icon's box: midway, by ink, between a centred 150 px title (a section's "Newly Added
 * 1/5" at 20 px, which ends at 262) and the glass. Stock's 50 px images (V1.36 images/xx, alpha
 * > 0): navbar_search's ink is columns 15 to 35, so 317 to 337 at FIND_X; song_order's
 * (s_img_order_navbar, the Sort of stock's Songs page) is columns 15 to 34, so 280 to 299 here:
 * 17 px after such a title's end and 17 px before the glass. */
#define SORT_X 265
#define TITLE_END (SORT_X + 15) /* 280: the sort's ink */
static void find_icon(int sort) {
    void *title = widget_get_child(pg.page, 0);
    int h = I(title, W_H), end = sort ? TITLE_END : FIND_X;
    widget_move_resize(title, 375 - end, 0, 2 * end - 375, h); /* centred: 95 to 280, or 73 to 302 */
    /* stock's sort style draws its image from the box's top left (draw type 0), where the glass's
     * centres it: a 50 px box centred on the title puts both at the same height. Made first: its box
     * overlaps the glass's (302 to 315), and a tap goes to the last child made, so the glass keeps
     * all of its own */
    if (sort) bar_icon(SORT_X, (h - 50) / 2, 50, "s_img_order_navbar", G_SORT);
    bar_icon(FIND_X, 0, h, "s_img_search", G_FIND);
}

/* Rows from.. added to the shown list in place of its trailing row, the rest left as they are;
 * ringnav_select gives ringnav the new count, so it keeps the selection and the scroll. A reload's
 * row (l->sel) is selected once its page is there, unless the user moved off the trailing row; once
 * reached or left it is the selection, so no later page moves it.
 * ponytail: the new count makes ringnav drop a spin's speed-up and big letter (load) at each page;
 * a ringnav call that grows a list in place would keep them while pages arrive. */
static void append(lv_t *l, int from) {
    if (!pg.view) return; /* the search box over it: show() draws them all when it closes */
    int cur = widget_get_prop_int(pg.view, "_ringnav_index", 0), n = l->n + (pg.f[3][0] != 0);
    if (cur == from && l->sel > from) cur = l->sel;
    if (n && cur >= n) cur = n - 1; /* past an empty last page: its last row */
    if (cur < n - 1 || !pg.paging || l->sel < cur) l->sel = cur; /* unless still on its way */
    if ((from + 1) * row_h(l) <=
        I(pg.view, W_H)) { /* page_list shrank a short first page: rebuilt */
        show();
        return;
    }
    widget_destroy(widget_get_child(pg.view, (unsigned)from));
    for (int i = from; i < n; ++i) row(l, i);
    widget_set_prop_int(pg.view, "virtual_h", n * row_h(l));
    ringnav_select(pg.view, cur, n);
}

/* A live level's new reply g (n rows) has the shown list's rows: as many, on one page, each of the
 * same kind, uri and context (so a press queued on one still does what it did) and with a detail
 * where it had one, More… shown or not as before, and as many covers. The shown reply's kinds, uris
 * and contexts are the new one's by then, so art_rows' answer is the new one's.
 * ponytail: covers are counted, not compared, so a cover swapped for another in one reply stays
 * until the next rebuild; a key per row if apps do that. */
static int same_rows(const lv_t *l, char **g, int n) {
    if (!pg.view || n != l->n || pg.pages != 1 || !g[3][0] != !pg.f[3][0]) return 0;
    for (int i = 0; i < n; ++i) {
        char **r = g + 4 + 6 * i;
        if (strcmp(r[0], cell(i, 0)) || strcmp(r[1], cell(i, 1)) || strcmp(r[4], cell(i, 4)) ||
            !r[3][0] != !cell(i, 3)[0])
            return 0;
    }
    return art_rows(n) == l->art;
}

/* Row item's label w, its title (k 2) or detail (3), says s, from its left in the face it needs;
 * a new title's marquee starts again at the next paint. */
static void retext(void *item, void *w, unsigned k, const char *s) {
    widget_set_text_utf8(w, s);
    theme_font(w, k == 2 ? ROLE_TALL : ROLE_META, FONT_ROW);
    widget_set_prop_int(w, "xoffset", 0);
    if (k == 2) marq_restart(item);
}

/* The shown rows' texts and progress bars written from the level's reply in place: a refresh that
 * only moves a percentage leaves the other rows, the selection and a long title's marquee as they
 * are, where a rebuild would start them over. A row's labels are its title then its detail, in
 * row()'s roles however it was dressed; one is set only when its text changed, read as UTF-8 (its
 * "text" prop is a wide string, which widget_get_prop_str doesn't return). A changed text starts
 * from its left, in the face it needs, and the selected row's new title has its marquee again at
 * the next paint (paint_selection), for its own width; a changed detail leaves the title's alone. */
static void relabel(const lv_t *l) {
    for (int i = 0; i < l->n; ++i) {
        void *item = widget_get_child(pg.view, (unsigned)i);
        for (unsigned c = 0, k = 2, m = widget_count_children(item); c < m; ++c) {
            void *w = widget_get_child(item, c);
            if (!tk_strcmp(widget_get_type(w), "hscroll_label")) {
                if (text_differs(w, cell(i, k))) retext(item, w, k, cell(i, k));
                ++k;
            } else if (widget_get_prop_int(w, "_pml", -1) >= 0) { /* a progress row's bar */
                set_pml(w, strtol(cell(i, 5), 0, 10));
                widget_invalidate_force(w, 0);
            }
        }
    }
}

/* The action's row says Working… (on) or its own text again, as the App Store's rows say
 * Installing…: its detail, or its title when it has none (an empty detail's label is under the
 * centred title). Its first two children are its labels (page_row_detail, art_row). */
static void busy_row(int on) {
    lv_t *l = top();
    int i = pg.arow;
    if (on == pg.busy || !pg.view || l->state != K_READY || i >= l->n) return;
    int k = 2 + !!cell(i, 3)[0];
    void *item = widget_get_child(pg.view, (unsigned)i);
    retext(item, widget_get_child(item, (unsigned)k - 2), (unsigned)k,
           on ? "Working…" : cell(i, k));
    pg.busy = on;
}

/* The top level: its rows, or what it waits for. */
static void show(void) {
    lv_t *l = top();
    int n = 0;
    /* a queued click names a row of the old list, so any rebuild drops it. ponytail: a short first
     * page that append() rebuilds drops it too, row kept or not; match the row by identity in go()
     * if that is ever pressed for. */
    if (pg.next == G_ROW) stop_timer(&pg.go);
    pg.view = pg.edit = 0;
    pg.busy = 0; /* new rows, their own texts: the tick says Working… again */
    if (l->state == K_LOAD && !((req.running || req.done) && !pg.act && req.id == l->id)) fetch(l);
    if (l->state == K_LOAD || l->state == K_WAIT) {
        char t[48];
        tk_snprintf(t, sizeof t, "Starting %s…", app()->name);
        if (!l->since) l->since = time_now_ms() | 1;
        l->slow = (int)(time_now_ms() - l->since) >= SLOW_MS && stoppable(); /* since: now + 1 */
        void *v =
            page_list(pg.page, pg.page, 0, l->state == K_WAIT ? t : "Loading…", n = l->slow, 48);
        if (n) pg.view = v, stop_row(0);
    } else if (l->state == K_ERROR) {
        n = 1 + (app()->nostop || stoppable());
        pg.view = page_list(pg.page, pg.page, 0, l->note, n, 48);
        page_row_detail(pg.view, 0,
                        l->retry ? "Retry" : "Install it again in System settings → App Store", 0,
                        clicked);
        if (app()->nostop)
            page_row_detail(pg.view, 1, "Cancel", 0, clicked);
        else if (n > 1)
            stop_row(1);
        page_push(pg.view, pg.moved), pg.moved = 0;
    } else {
        /* ponytail: the first page decides, as pages appended later (append) keep this drawing's
         * choice and Back or an action reloads from the first page; a list rebuilt whole with all
         * its pages there (a short first page, the search box closing) counts them */
        art_forget(); /* the rows are built anew: nothing of theirs decoded yet */
        l->art = art_rows(l->n);
        pg.view = page_list(pg.page, pg.page, 0, l->title, n = l->n + (pg.f[3][0] != 0),
                            l->art ? ART_ROW_H : 64);
        for (int i = 0; i < n; ++i) row(l, i);
        if (l->find) find_icon(l->sort);
        page_push(pg.view, pg.moved), pg.moved = 0;
    }
    if (n) ringnav_select(pg.view, l->state == K_ERROR ? 0 : l->sel < n ? l->sel : n - 1, n);
    art_window();
}

/* The selected row kept, from a page showing the level's own rows (not an error or a wait's). */
static void keep_sel(void) {
    if (pg.view && top()->state == K_READY)
        top()->sel = widget_get_prop_int(pg.view, "_ringnav_index", 0);
}

/* A level over the top one, which lets its reply go (Back asks for it again); base: args' bytes
 * that name its list, before a More…'s cursor. */
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
    drop();
    l->n = 0, l->state = K_LOAD;
    pg.lv[pg.depth++] = next;
    pg.moved = 1;
    show();
}

/* ponytail: Back pages the level below again from its first page, where keeping a paged level's
 * replies under a pushed one would spare it. */
static void pop(void) {
    req_cancel();
    drop();
    --pg.depth;
    top()->state = K_LOAD;
    pg.moved = -1;
    show();
}

/* Section i shown as the first level: its op asked, its list sliding in from dir's side (1 from the
 * right, as the next; -1 from the left; 0 in place), the one it replaces keeping its selected row.
 * With two or more the title has the position, Albums 2/3. */
static void sec_open(int i, int dir) {
    lv_t *l = &pg.lv[0];
    req_cancel();
    keep_sel();
    pg.sec[pg.cur].sel = l->sel;
    drop();
    pg.cur = (signed char)i;
    *l = pg.sec[i]; /* K_LOAD */
    if (pg.nsec > 1)
        tk_snprintf(l->title, sizeof l->title, "%s %d/%d", pg.sec[i].title, i + 1, pg.nsec);
    pg.moved = (signed char)dir;
    show();
}

/* A page row's press: the top level's list from cursor c, in the level's place, so Back leaves the
 * list instead of stepping back through its pages; dir: 1 a later page, -1 an earlier one. */
static void page_to(lv_t *l, const char *c, int dir) {
    char a[sizeof l->args] = "";
    if (!cursor_args(l, a, sizeof a, c)) return;
    /* the first content row: past Previous page, and past a Search and a Sort row kept as rows */
    int first = !strcmp(cell(0, 0), "search");
    first += first < l->n && !strcmp(cell(first, 0), "sort");
    ++first;
    req_cancel();
    drop();
    memcpy(l->args, a, sizeof a);
    l->n = 0, l->state = K_LOAD;
    l->sel = dir > 0 ? first : 1 << 20; /* show() clamps it to the last row */
    pg.moved = (signed char)dir;
    show();
}

/* The first level's reply saying "sections": its list rows (n of g's) become the sections, and the
 * first opens. 0 when it has none to take. */
static int sec_take(char **g, int n) {
    if (!pg.sec && !(pg.sec = calloc(ADDON_SECTIONS, sizeof *pg.sec))) return 0;
    int k = 0;
    for (int i = 0; i < n && k < ADDON_SECTIONS; ++i) {
        char **c = g + 4 + 6 * i;
        if (strcmp(c[0], "list") || !c[1][0]) continue;
        lv_t *s = &pg.sec[k];
        tk_snprintf(s->op, sizeof s->op, "%s", c[1]);
        tk_snprintf(s->title, sizeof s->title, "%s", c[2]);
        s->args[0] = 0;
        if (c[4][0] && !put(s->args, sizeof s->args, c[4])) continue; /* too long: left out */
        s->base = strlen(s->args);
        ++k;
    }
    if (!k) return 0;
    pg.nsec = (signed char)k;
    sec_open(0, 0);
    return 1;
}

/* The finished request: an action's toast, or the top level's rows. */
static void got(void) {
    lv_t *l = top();
    char id[12], *head[4], *g[4 + 6 * ADDON_ROWS], *reply = req.buf, *fit;
    /* its own size, before spot_reply points into it: a full list keeps ADDON_PAGES of them */
    if (req.fail == REQ_OK && (fit = realloc(reply, req.n + 1))) reply = fit;
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
    if (act) busy_row(0); /* before the toast, whose loop can pop the level */
    pg.act = 0;
    if (act) {
        if (n >= 0 && f[2][0])
            toast(f[2]);
        else if (!ok)
            toast(why);
        if (ok && !strcmp(pg.aop, "stop")) {
            app()->launched = app()->hold = 0;
            navigator_back();
        } else if (ok && l == top() && l->state == K_READY && l->live)
            keep_sel(), fetch(l); /* a live level stays shown, asked again now: no Loading… page */
        else
            keep_sel(), l->state = K_LOAD, show(); /* the level as the action left it */
    } else if (l->id != req.id) {
    } else if (pg.paging) { /* a next page appended, or the list ends at More… (rows kept) */
        int from = l->n;
        pg.paging = 0;
        if (ok) {
            pg.buf[pg.pages++] = reply, reply = 0;
            memcpy(pg.f + 4 + 6 * from, g + 4, 6 * (unsigned)n * sizeof *g);
            pg.f[3] = g[3], l->n += n;
            if (!strcmp(g[2], "pages") && g[3][0] && pg.pages < ADDON_PAGES) page_next(l);
        }
        append(l, from);
    } else if (req.fail == REQ_NOSOCK &&
               (app()->restart || now - app()->launch_at < ADDON_START_MS)) {
        if (l->state != K_WAIT && l->state != K_READY) l->state = K_WAIT, show();
        pg.again = now + 500;
    } else if (ok && pg.depth == 1 && !pg.nsec && !strcmp(f[2], "sections") && sec_take(g, n)) {
        /* sections: the first asked for (sec_open); this reply's rows were their names. Taken once:
         * a section that answers "sections" too is a plain list, not asked for again and again */
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
            int find = take_find(g, &n), sort = find && take_sort(g, &n);
            int same = l->state == K_READY && l->live && find == l->find && sort == l->sort &&
                       same_rows(l, g, n);
            keep_sel();
            if (same)
                free(pg.buf[0]); /* same_rows: its one page, and the covers stay drawn */
            else
                drop();
            pg.buf[0] = reply, pg.pages = 1, reply = 0;
            memcpy(pg.f, f, sizeof g); /* pg.f holds ADDON_PAGES replies' rows, g one's */
            l->n = n;
            l->find = find;
            l->sort = sort;
            l->hash = hash;
            l->state = K_READY;
            /* an app opts in; older ones' lists keep More… */
            if (!strcmp(f[2], "pages") && f[3][0]) page_next(l);
            if (same)
                relabel(l); /* only texts and bars moved: the rows stay */
            else
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
    /* Loading… does nothing: dropped here, as by go() its page may have put a row at its index */
    if (pg.paging && (int)(long)ctx == top()->n) return 0;
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

/* The page's touches (page_at binds them, before its children see them): a sideways swipe on the
 * first level of an app with sections moves to the next or the previous one, as Pull to search's
 * pull (navigation.c pull_event): once the move is a swipe's, the rows it pressed are told the
 * touch is gone, so the lift clicks nothing, and the moves and the lift are kept from the list. Up
 * and down stay the list's.
 * ponytail: touch only. The wheel moves the selection, as on every list, so the sections past the
 * first are reached by a swipe alone; bind a key here if that is ever asked for. */
static int swipe(void *ctx, void *event) {
    (void)ctx;
    int type = I(event, EVENT_TYPE);
    if (type == EVT_POINTER_ABORT || pg.nsec < 2 || pg.depth != 1 || pg.edit) {
        pg.sdown = pg.sclaim = 0;
        return 0;
    }
    int x = I(event, EVENT_X), y = I(event, EVENT_Y);
    if (type == EVT_POINTER_DOWN) {
        pg.sdown = 1, pg.sclaim = 0, pg.sx = x, pg.sy = y;
        return 0;
    }
    if (!pg.sdown) return 0;
    int dx = x - pg.sx, dy = y - pg.sy, ax = dx < 0 ? -dx : dx, ay = dy < 0 ? -dy : dy;
    if (type == EVT_POINTER_UP_BEFORE) {
        int claimed = pg.sclaim;
        pg.sdown = pg.sclaim = 0;
        if (!claimed) return 0;
        if (ax >= SWIPE_PX && ax > ay) later(G_SECTION, dx < 0 ? 1 : -1); /* a swipe to the left: the next */
        return 11; /* RET_STOP */
    }
    if (type != EVT_POINTER_MOVE_BEFORE) return 0;
    if (!pg.sclaim) {
        if (ay >= SWIPE_CLAIM_PX) { /* the list's: it scrolls */
            pg.sdown = 0;
            return 0;
        }
        if (ax < SWIPE_CLAIM_PX || ax < 2 * ay) return 0;
        unsigned abort_event[12];
        pointer_event_init(abort_event, EVT_POINTER_ABORT, pg.page, pg.sx, pg.sy);
        widget_dispatch_event_to_target_recursive(pg.page, abort_event);
        pg.sdown = pg.sclaim = 1; /* after: the abort comes back here and clears them */
        if (pg.view) list_stop_scroll(pg.view); /* as pull_event: a fling under the claim ends */
    }
    return 11; /* RET_STOP */
}

/* peq_ui.c's T9 edit over the title, its keyboard open: a Search row's press, or its icon's tap. */
static void search_box(void) {
    keep_sel();
    widget_destroy_children(pg.page), pg.view = 0; /* the box alone, as Spotify's */
    pg.edit = edit_create(pg.page, "text", 2, 26);
    t9_box(pg.page, pg.edit, app()->tips);
    widget_on(pg.edit, EVT_VALUE_CHANGED, searched, 0);
    widget_on(pg.edit, EVT_IM_ACTION, searched, 0);
    widget_on(pg.edit, EVT_BLUR, search_left, 0);
    widget_set_focused(pg.edit, 1);
}

/* A title bar icon's tap, ctx its G_ code, on a timer as a row's: what it opens replaces the page
 * the icon is in. */
static int bar_tapped(void *ctx, void *event) {
    (void)event;
    later((int)(long)ctx, 0);
    return 0;
}

/* A list row's press, or the sort icon's tap: level uri over the top one, its context the
 * argument, titled title. */
static void open_list(const char *uri, const char *ctx, const char *title) {
    char a[sizeof top()->args] = "";
    if (!ctx[0] || put(a, sizeof a, ctx)) push(uri, a, strlen(a), title);
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
    else if (pg.next == G_SECTION) { /* a swipe: the next or previous section, none past the ends */
        int to = pg.cur + i;
        if (pg.depth == 1 && to >= 0 && to < pg.nsec) sec_open(to, i);
    } else if (pg.next == G_FIND) {
        if (l->state == K_READY && l->find) search_box();
    } else if (pg.next == G_SORT) { /* the sort icon, opening as its row would */
        if (l->state == K_READY && l->sort) open_list(pg.sort[1], pg.sort[4], pg.sort[2]);
    } else if (pg.next == G_SEARCH) {
        if (put(a, sizeof a, pg.term)) push("search", a, strlen(a), pg.term);
    } else if (l->state == K_ERROR && i == 1 && app()->nostop) { /* Cancel, as Return */
        if (pg.depth > 1)
            pop();
        else
            navigator_back();
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
    } else if (i == l->n) { /* More…, as a level of its own; Loading… does nothing */
        if (!pg.paging && cursor_args(l, a, sizeof a, pg.f[3]))
            push(l->op, a, l->base, pg.depth == 1 && pg.nsec ? pg.sec[pg.cur].title : l->title);
    } else {
        const char *kind = cell(i, 0), *uri = cell(i, 1), *ctx = cell(i, 4);
        if (!strcmp(kind, "list") || !strcmp(kind, "sort")) /* with remote and a folder: opens() */
            open_list(uri, ctx, cell(i, 2));
        else if (!strcmp(kind, "page"))
            page_to(l, ctx, strcmp(uri, "prev") ? 1 : -1);
        else if (!strcmp(kind, "action") && !pg.act) /* one in flight: this press does nothing */
            action(i, uri, ctx);
        else if (!strcmp(kind, "remote"))
            qr_open(app()->state, app()->name);
        else if (!strcmp(kind, "local")) { /* a song, or a folder of them, on the card alone */
            if (card_path(uri))
                songs_open(uri, cell(i, 2), !strcmp(ctx, "album"));
        } else if (!strcmp(kind, "search"))
            search_box();
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
    else if (pg.act && (int)(now - pg.acted) >= BUSY_MS)
        busy_row(1);
    else if ((l->state == K_LOAD || l->state == K_WAIT) && !l->slow && l->since &&
             (int)(now - l->since) >= SLOW_MS && stoppable())
        show();
    else if (!req.running && (int)(now - pg.again) >= 0 && !pg.edit &&
             (l->state == K_WAIT || (l->state == K_READY && l->live && g_backlight_status &&
                                     window_manager_get_top_window(window_manager()) == pg.page)))
        fetch(l);
    art_window();
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
    (void)event;
    if (ctx != pg.page) return 0; /* a page addon_goto replaced, its state gone already */
    req_cancel();
    stop_timer(&pg.timer);
    stop_timer(&pg.go);
    stop_timer(&pg.leave);
    drop();
    free(pg.lv);
    free(pg.f);
    free(pg.sec);
    memset(&pg, 0, sizeof pg);
    return 0;
}

/* Slot k's page, its first level op (args: "\t" and escaped each) titled title: the app started if
 * it isn't (its QR code shown at the first answer when qr); gone from the card, the page says so.
 * 1 when it opened or said why not. */
static int page_at(int k, const char *op, const char *args, const char *title, int qr) {
    const char *busy = shelf_busy(); /* a job on this app's folder */
    app_t *b = &ap.a[k];
    /* A page is refused when the folder is being deleted or moved into, and while an update
     * downloads for an app that is not running: launch() would be refused too, a dead page. */
    if (busy && !strcmp(busy + 1, b->id) &&
        (shelf_moving() || !b->launched || access(b->pid, 0)))
        return toast("Wait until the App Store finishes"), 1;
    if (pg.page || !(pg.lv = calloc(ADDON_DEPTH, sizeof *pg.lv))) return 0;
    if (!(pg.f = calloc(4 + 6 * ADDON_ROWS * ADDON_PAGES, sizeof *pg.f)) ||
        !(pg.page = page_open("addon_page", closed, keyup)))
        return free(pg.lv), free(pg.f), pg.lv = 0, pg.f = 0, 0;
    pg.a = k;
    app_t *a = app();
    int missing = access(a->bin, 0);
    if (!missing && (!a->launched || access(a->pid, 0))) launch(a), pg.code = qr && a->qr;
    pg.depth = 1;
    static const unsigned short touches[] = { EVT_POINTER_DOWN, EVT_POINTER_MOVE_BEFORE,
                                              EVT_POINTER_UP_BEFORE, EVT_POINTER_ABORT };
    for (unsigned t = 0; t < sizeof touches / sizeof *touches; ++t)
        widget_on(pg.page, touches[t], swipe, 0);
    lv_t *l = top();
    tk_snprintf(l->op, sizeof l->op, "%s", op);
    tk_snprintf(l->args, sizeof l->args, "%s", args);
    l->base = strlen(l->args);
    tk_snprintf(l->title, sizeof l->title, "%s", title);
    if (missing) {
        l->state = K_ERROR;
        tk_snprintf(l->note, sizeof l->note, "%s isn't on the card", a->name);
    }
    pg.timer = timer_add(tick, 0, 250);
    show();
    return 1;
}

/* An app's Streaming row (home.c ringnav_stream; ctx: its place by name at the last scan). */
int addon_open(void *ctx, void *event) {
    (void)event;
    int k = (int)(long)ctx;
    if (smart_hold(addon_open, ctx, 0)) return 0; /* the app starts once Smart Wi-Fi's link is up */
    if (k >= 0 && k < ap.shown) page_at(ap.order[k], "menu", "", ap.a[ap.order[k]].name, 1);
    return 0;
}

/* Whether path is in an app's folder, CARD/.<id>/, and id the app's. */
static int card_id(const char *path, char *id) {
    const char *p = path && tk_str_start_with(path, CARD "/.") ? path + sizeof CARD "/." - 1 : 0;
    unsigned k = 0;
    while (p && p[k] && p[k] != '/' && k < APP_ID - 1) ++k; /* an id fits id with its NUL */
    if (!p || p[k] != '/') return 0;
    tk_snprintf(id, k + 1, "%s", p);
    return valid_id(id);
}
/* The app whose folder holds path, when its q2app says goto=1; 0 for any other. */
static app_t *goto_app(const char *path) {
    char id[APP_ID], raw[512], file[64];
    if (!card_id(path, id)) return 0;
    tk_snprintf(file, sizeof file, CARD "/.%s/q2app", id);
    if (!state_read(file, raw, sizeof raw) || number(raw, "goto") != 1) return 0;
    app_t *a = slot(id);
    if (a && !a->name[0]) addon_scan(); /* not listed by Streaming since boot: its name read */
    return a && a->present ? a : 0;
}
/* The hold menu's: whether Go to on path opens a page, its app's (goto=1) or, outside an app's
 * folder, stock's. An app's folder is hidden, so the Library never holds its songs. */
int addon_goes(const char *path) {
    char id[APP_ID];
    return !card_id(path, id) || goto_app(path);
}
/* Whether a listen of path goes in the scrobble log (navigation.c scrobble): not when it is in an
 * app's folder and its q2app says scrobble=0, a line a Windows editor may end in \r. Compared as
 * text: number() is 0 for a missing key or a word too.
 * ponytail: by folder, not by who queued it: a song an app's .m3u or local row names outside its
 * own folder still logs. Knowing the app means keeping it per queue, if one ever needs that. */
int addon_scrobbles(const char *path) {
    char id[APP_ID], raw[512], file[64], v[4];
    if (!card_id(path, id)) return 1;
    tk_snprintf(file, sizeof file, CARD "/.%s/q2app", id);
    state_read(file, raw, sizeof raw);
    field(raw, "scrobble", v, sizeof v);
    return strcmp(v, "0") && strcmp(v, "0\r");
}

/* Go to Album (album) or Go to Artist on a song in a card app's folder, /mnt/mmc/.<id>/…
 * (navigation.c qm_goto): when its manifest says goto=1, its page opens at op "goto" ("album" or
 * "artist", then the song's path), the level the app answers with, titled title; Back leaves it
 * for the page under it. Its page already open (the song played from it) is closed first, as
 * Coverflow's scoped page is: one page's state. 1 when it opened. */
int addon_goto(const char *path, const char *title, int album) {
    char args[sizeof pg.lv->args] = "";
    app_t *a = goto_app(path);
    if (!a || !put(args, sizeof args, album ? "album" : "artist") || !put(args, sizeof args, path))
        return 0;
    void *old = pg.page;
    if (old) closed(old, 0), window_manager_close_window_force(window_manager(), old);
    return page_at((int)(a - ap.a), "goto", args, title && *title ? title : a->name, 0);
}

/* ringnav_keylong: Play/Pause held on a row with a hold op (a list: all of it downloaded) sends it;
 * 1 when it was one. */
int addon_hold(void *w) {
    lv_t *l = pg.page && w == pg.page ? top() : 0;
    int i = l && l->state == K_READY && pg.view ? widget_get_prop_int(pg.view, "_ringnav_index", -1)
                                                : -1;
    if (i < 0 || i >= l->n || !cell(i, 5)[0] || !strcmp(cell(i, 0), "progress")) return 0;
    action(i, cell(i, 5), cell(i, 4));
    return 1;
}

/* The QR code page (qrcode_page): an app's status file's qr (qrsize rows of hex, the leftmost
 * module the high bit), drawn on a white plate, beside its address (addr), PIN (pin) and note,
 * read again every second. Without an address (no Wi-Fi) or a file (not running), it says so. */
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

/* Classic's QR page: its layout, and a label in the info role's face for the text it holds now,
 * at the label's own size.
 * ponytail: a font-only theme_font role would save the size rewrite. */
static int qr_light(void) { return classic_pages(); }
static void *qr_face(void *l, int px) {
    if (qr_light()) {
        theme_font(l, ROLE_INFO, FONT_ROW);
        widget_set_prop_int(l, "style:normal:font_size", px);
    }
    return l;
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
                        qr_light()          ? 19
                        : strlen(addr) > 17 ? 14
                                            : 16); /* 192.168.100.200:8080 */
    set(qp.addr, addr);
    set(qp.pin, strlen(pin) == 6 ? spaced : pin);
    set(qp.note, note);
    qr_face(qp.note, qr_light() ? 16 : 14);
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

static void *qr_label(void *parent, int x, int y, int w, int h, const char *style, int px,
                      unsigned color) {
    return qr_face(label(parent, x, y, w, h, style, px, color), px);
}

static void qr_open(const char *state, const char *title) {
    if (qp.page || !(qp.hex = calloc(QR_HEX, 1))) return;
    if (!(qp.page = page_open("qrcode_page", qr_closed, qr_keyup))) {
        free(qp.hex), qp.hex = 0;
        return;
    }
    void *f = widget_factory(), *page = qp.page;
    int h = widget_get_prop_int(page, "h", client_h()), cl = qr_light();
    /* the two text lines, or open, the address, PIN, its value, the note: y, height, px */
    static const short rows[2][7][3] = {
        { { 2, 22, 18 },
          { 24, 22, 18 },
          { 60, 18, 14 },
          { 78, 22, 16 },
          { 112, 18, 14 },
          { 130, 28, 24 },
          { 172, 20, 14 } },
        { { 17, 23, 19 },
          { 38, 23, 19 },
          { 71, 20, 16 },
          { 88, 23, 19 },
          { 119, 20, 16 },
          { 133, 28, 24 },
          { 213, 20, 16 } },
    };
    const short (*r)[3] = rows[cl];
    /* Classic's column has the page, as its note runs under the plate; its name is the bar's
     */
    int x = cl ? 200 : 0, w = cl ? 163 : 375 - QR_COL - 12, short_w = cl ? 163 : 140;
    qp.state = state;
    void *name = qr_label(page, 16, 12, 240, 16, "s_scrlabel_white20l", 16, SPOT_GREY);
    widget_set_text_utf8(name, title);
    qr_face(name, 16);
    if (cl) {
        widget_set_name(name, PAGE_TITLE);
        widget_set_visible(name, 0, 0);
    }
    qp.qr = cl ? widget_factory_create_widget(f, "view", page, 22, 18, QR_LIGHT_PX, QR_LIGHT_PX)
               : widget_factory_create_widget(f, "view", page, 16, 40, QR_PX, QR_PX);
    qp.col = cl ? widget_factory_create_widget(f, "view", page, 0, 0, 375, h)
                : widget_factory_create_widget(f, "view", page, QR_COL, 40, w, QR_PX);
    widget_set_text_utf8(
        qr_label(qp.col, x, r[0][0], short_w, r[0][1], "s_scrlabel_white20l", r[0][2], 0xffffffffu),
        cl ? "Scan with your phone's" : "Scan with your");
    widget_set_text_utf8(
        qr_label(qp.col, x, r[1][0], short_w, r[1][1], "s_scrlabel_white20l", r[1][2], 0xffffffffu),
        cl ? "camera" : "phone's camera");
    widget_set_text_utf8(
        qr_label(qp.col, x, r[2][0], short_w, r[2][1], "s_scrlabel_white20l", r[2][2], SPOT_GREY),
        "or open");
    qp.addr = qr_label(qp.col, x, r[3][0], w, r[3][1], "s_scrlabel_white20l", r[3][2], 0xffffffffu);
    widget_set_text_utf8(
        qr_label(qp.col, x, r[4][0], short_w, r[4][1], "s_scrlabel_white20l", r[4][2], SPOT_GREY),
        "PIN");
    qp.pin =
        qr_label(qp.col, x, r[5][0], short_w, r[5][1], "s_scrlabel_white20l", r[5][2], 0xffffffffu);
    qp.note = qr_label(qp.col, cl ? 24 : 0, r[6][0], cl ? 330 : w, r[6][1], "s_scrlabel_white20l",
                       r[6][2], SPOT_GREY);
    void *scroll[] = { qp.addr, qp.note };
    for (unsigned i = 0; i < 2; ++i)
        hscroll_label_set_only_focus(scroll[i], 0), hscroll_label_set_ellipses(scroll[i], 0);
    qp.msg = widget_factory_create_widget(f, "view", page, 0, 0, 375, h);
    qp.say = qr_label(qp.msg, CF_EDGE, 100, 375 - 2 * CF_EDGE, 30, "s_scrlabel_white20c", 20,
                      0xffffffffu);
    qp.sub =
        qr_label(qp.msg, CF_EDGE, 134, 375 - 2 * CF_EDGE, 24, "s_scrlabel_white20c", 16, SPOT_GREY);
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

/* ringnav_paint, every widget after stock painted it: a progress row's bar, a flat rectangle filled
 * position/1000 of its width from the left, square, with no knob (the accent's light tone, as
 * Spotify's bar, over a dim track; the scrubber's rows under a theme that has them); and the code,
 * dark modules on the white plate, a row's run of them one rectangle, inside a quiet zone of two
 * modules. */
void addon_paint(void *w, void *canvas) {
    /* the height first: no prop lookup for the page's other widgets */
    int pml = pg.page && I(w, W_H) == progress_h() && P(canvas, CANVAS_LCD)
                  ? widget_get_prop_int(w, "_pml", -1)
                  : -1;
    if (pml >= 0) {
        unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
        int bw = I(w, W_W), h = I(w, W_H), x = bw * pml / 1000;
        if (theme()->painter[PAINT_SCRUB] == SCRUB_ROWS) {
            paint_rows_bar(canvas, 0, 0, bw, h, x);
            return;
        }
        canvas_set_fill_color(canvas, rgba(PROGRESS_TRACK, 255));
        canvas_fill_rect(canvas, 0, 0, bw, h);
        canvas_set_fill_color(canvas, rgba(accent_tone(2), 255));
        if (x) canvas_fill_rect(canvas, 0, 0, x, h);
        stock_canvas_set_fill_color(canvas, fill);
        return;
    }
    if (!qp.page || w != qp.qr || !P(canvas, CANVAS_LCD) || qp.size < 21) return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    int side = qr_light() ? QR_LIGHT_PX : QR_PX;
    int n = qp.size, m = side / (n + 4), o = (side - m * n) / 2, digits = (n + 3) / 4;
    unsigned raw = ui_ink_raw(qr_light()); /* Classic's code reads dark on white in either half */
    if (qr_light()) plate(canvas, -1, -1, side + 2, side + 2, 0x22000000u, 9);
    plate(canvas, 0, 0, side, side, 0xffffffffu, qr_light() ? 8 : 10);
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
    ui_ink_raw(raw);
    stock_canvas_set_fill_color(canvas, fill);
}
