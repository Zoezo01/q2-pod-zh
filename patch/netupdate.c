/* Network Update (docs/internals.md#network-update): System Update's second row fetches the latest
 * Q2 Pod release from GitHub to the card's update.tar, then hands over to stock's TF card update. */
#include "peq.h"

/* The tag in a releases/latest redirect, ".../releases/tag/1.3.4": what follows the last '/'. */
const char *netupdate_tag(const char *location) {
    const char *slash = location ? strrchr(location, '/') : 0;
    return slash && strstr(location, "/releases/tag/") && slash[1] ? slash + 1 : 0;
}

/* Q2 Shelf (docs/internals.md#q2-shelf): the apps in sources.list (fetched from q2-pod's repo,
 * cached on the card) from their GitHub releases' zips, unpacked by unzip into the card's staging
 * folder, then moved into place file by file. */
#ifdef PEQ_HOST
#include <sys/stat.h>
/* Info-ZIP's takes the same options; quiet, as it warns of -x patterns nothing matched. */
#define UNZIP "unzip 2>/dev/null"
#else
#define UNZIP "busybox unzip" /* stock busybox 1.31.1 */
#endif
#define CARD PEQ_ROOT "/mnt/mmc"
#define SH_DIR CARD "/.q2"
#define STAGING SH_DIR "/staging"
#define SH_CAP (1u << 30) /* the most an install may unpack */
#define SH_MAX 16

enum { SH_OK, SH_NET, SH_SPACE, SH_CARD, SH_MISSING, SH_BAD, SH_CANCELLED, SH_BUSY };
/* repo: its GitHub owner/name; id: the repo's name less "q2-", for the card folder .<id>, the tag
 * and file list; keep: unzip -x patterns an update never writes (the user's); bin: the app's
 * binary, present without a tag file meaning a manual install, and moved after every other file:
 * power-on runs Rockbox's. */
typedef struct {
    char name[32], id[16], repo[80], dir[17], bin[40], latest[32];
    const char *keep;
} shelf_app;
shelf_app shelf_apps[SH_MAX];
int shelf_n;
#define apps shelf_apps

/* Q2 Pod's own, whose folders, binaries and kept files differ from a card app's (addon.c: .<id>,
 * .<id>/q2-<id>, nothing kept); matched by the repo's name, so a fork is the same app. */
static const struct {
    const char *repo, *id, *dir, *keep, *bin;
} special[] = {
    { "q2-rockbox", "rockbox", ".rockbox", ".rockbox/config.cfg", ".rockbox/rockbox" },
    { "q2-rockbox-themes", "themes", ".rockbox", ".rockbox/config.cfg", "" },
    { "q2-librespot", "spotify", ".spotify", "'.spotify/cache/*' .spotify/.cache-size",
      ".spotify/librespot" },
};

/* Whether s's n bytes are all a-z0-9- (kind 0, an id), or A-Z too (1, an owner), or ._ too (2). */
static int only(const char *s, unsigned n, int kind) {
    for (unsigned i = 0; i < n; ++i) {
        char c = s[i];
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' ||
              (kind && c >= 'A' && c <= 'Z') || (kind > 1 && (c == '.' || c == '_'))))
            return 0;
    }
    return n > 0;
}

/* sources.list's lines, "owner/repo [Name]" ("#" a comment), into out: at most max, each owner,
 * repo and id checked (they go into URLs and shell commands; the id as addon.c's, 1 to 15 of
 * a-z0-9-), a bad or repeated id's line skipped. The count. */
int shelf_sources(const char *text, shelf_app *out, int max) {
    int n = 0;
    for (const char *p = text, *e; p && *p && n < max; p = *e ? e + 1 : e) {
        e = strstr(p, "\n");
        e = e ? e : p + strlen(p);
        while (p < e && (*p == ' ' || *p == '\t')) ++p;
        const char *end = e, *slash = p, *sp = p;
        while (end > p && (unsigned char)end[-1] <= ' ') --end; /* "\r" too */
        while (sp < end && *sp != ' ' && *sp != '\t') ++sp;
        while (slash < sp && *slash != '/') ++slash;
        const char *repo = slash + 1, *name = sp;
        while (name < end && (*name == ' ' || *name == '\t')) ++name;
        unsigned owner = slash - p, rn = sp < repo ? 0 : sp - repo, nn = end - name;
        if (p == end || *p == '#') continue;
        if (owner > 39 || !only(p, owner, 1) || !only(repo, rn, 2) ||
            owner + 1 + rn >= sizeof out->repo || nn >= sizeof out->name)
            continue;
        int bad = 0;
        for (const char *c = name; c < end; ++c) bad |= (unsigned char)*c < ' ' || *c == 127;
        shelf_app a = { 0 };
        memcpy(a.repo, p, owner + 1 + rn);
        snprintf(a.name, sizeof a.name, "%.*s", (int)(nn ? nn : rn), nn ? name : repo);
        const char *id = rn > 3 && !memcmp(repo, "q2-", 3) ? repo + 3 : repo;
        unsigned in = repo + rn - id;
        for (unsigned k = 0; k < sizeof special / sizeof *special; ++k) {
            if (strlen(special[k].repo) == rn && !memcmp(special[k].repo, repo, rn)) {
                snprintf(a.id, sizeof a.id, "%s", special[k].id);
                snprintf(a.dir, sizeof a.dir, "%s", special[k].dir);
                snprintf(a.bin, sizeof a.bin, "%s", special[k].bin);
                a.keep = special[k].keep;
            } else if (strlen(special[k].id) == in && !memcmp(special[k].id, id, in))
                bad = 1; /* another repo's "spotify" is not Q2 Pod's */
        }
        if (!*a.id) {
            if (in > 15 || !only(id, in, 0)) continue;
            memcpy(a.id, id, in);
            snprintf(a.dir, sizeof a.dir, ".%s", a.id);
            snprintf(a.bin, sizeof a.bin, ".%s/q2-%s", a.id, a.id);
            a.keep = "";
        }
        for (int k = 0; k < n; ++k) bad |= !strcmp(out[k].id, a.id);
        if (!bad) out[n++] = a;
    }
    return n;
}

/* The value after key in json, past its colon; 0 if none. */
static const char *json_at(const char *json, const char *key) {
    const char *p = json ? strstr(json, key) : 0;
    if (!p) return 0;
    for (p += strlen(key); *p == ' ' || *p == ':'; ++p) {}
    return p;
}

/* The JSON string at p into out: none with escapes, which GitHub's tags, names and URLs lack. */
static int json_string(const char *p, char *out, unsigned n) {
    unsigned i = 0;
    if (!p || *p++ != '"') return 0;
    for (; p[i] && p[i] != '"' && p[i] != '\\' && i + 1 < n; ++i) out[i] = p[i];
    out[i] = 0;
    return i && p[i] == '"';
}

/* GitHub's releases/latest JSON: the tag, and the first asset whose name ends in .zip, its URL and
 * size. 0 when any is missing. */
int shelf_release(const char *json, char *tag, unsigned tn, char *url, unsigned un,
                  unsigned *size) {
    char name[128];
    if (!json_string(json_at(json, "\"tag_name\""), tag, tn)) return 0;
    for (const char *p = json_at(json, "\"assets\""); (p = json_at(p, "\"name\""));) {
        unsigned n = json_string(p, name, sizeof name) ? strlen(name) : 0;
        if (n > 4 && !strcmp(name + n - 4, ".zip")) {
            const char *s = json_at(p, "\"size\"");
            *size = s ? (unsigned)atoi(s) : 0;
            return *size && json_string(json_at(p, "\"browser_download_url\""), url, un);
        }
    }
    return 0;
}

/* unzip -l's listing of what an install unpacks (busybox's and Info-ZIP's columns: the size, then
 * the name 21 bytes after it): the entries, *total their bytes; -1 for an absolute path, a ".."
 * component, or over cap bytes in all. busybox strips a leading "/" and "../" itself before both
 * listing and unpacking, so this catches only what it leaves. */
int shelf_unpacked(const char *listing, unsigned cap, unsigned *total) {
    unsigned long long sum = 0;
    int n = 0;
    for (const char *p = listing, *e; p && *p; p = *e ? e + 1 : e) {
        const char *s = p, *d;
        e = strstr(p, "\n");
        e = e ? e : p + strlen(p);
        while (*s == ' ') ++s;
        for (d = s; *d >= '0' && *d <= '9'; ++d) {}
        if (d == s || d[0] != ' ' || d[1] != ' ' || d[2] < '0' || d[2] > '9' || e - d <= 21)
            continue; /* the header, its rules and the total */
        sum += (unsigned)atoi(s);
        ++n;
        if (d[21] == '/') return -1;
        for (const char *c = d + 21, *q; c <= e; c = q + 1) {
            for (q = c; q < e && *q != '/'; ++q) {}
            if (q - c == 2 && c[0] == '.' && c[1] == '.') return -1;
        }
    }
    if (sum > cap) return -1;
    *total = (unsigned)sum;
    return n;
}

/* Whether path is one of list's n bytes of NUL-terminated lines. */
int shelf_listed(const char *list, unsigned n, const char *path) {
    for (const char *p = list; p && p < list + n; p += strlen(p) + 1)
        if (!strcmp(p, path)) return 1;
    return 0;
}

/* A file of lines (find's), each NUL-terminated in place; n its bytes. */
static char *lines(const char *path, unsigned *n) {
    char *p = read_all(path, 4u << 20, n);
    for (unsigned i = 0; p && i < *n; ++i)
        if (p[i] == '\n') p[i] = 0;
    return p;
}

/* unzip with opts (and the shell's redirect, after) on app i's download: only its folder, never
 * macOS's ._ files or the kept paths. */
static int unzip(int i, const char *opts, const char *after) {
    char cmd[400];
    snprintf(cmd, sizeof cmd, UNZIP " %s " STAGING "/%s.part '%s/*' -x '*/._*' %s%s", opts,
             apps[i].id, apps[i].dir, apps[i].keep, after);
    return system(cmd);
}

static void staged(char *out, unsigned n, int i, const char *ext) {
    snprintf(out, n, STAGING "/%s%s", apps[i].id, ext);
}

/* The download's listing checked: SH_OK and the bytes it unpacks to in *total, or SH_BAD. */
int shelf_list(int i, unsigned *total) {
    char path[96], after[100];
    unsigned n = 0;
    staged(path, sizeof path, i, ".list");
    snprintf(after, sizeof after, " > %s", path);
    char *listing = unzip(i, "-l -qq", after) ? 0 : read_all(path, 1u << 20, &n);
    int entries = listing ? shelf_unpacked(listing, SH_CAP, total) : -1;
    free(listing);
    unlink(path);
    return entries > 0 ? SH_OK : SH_BAD;
}

/* Unpacked into staging/<id>/. ponytail: the card is FAT32 or exFAT, neither holding symlinks or
 * special files: busybox fails on one (its own error), so nothing checks for them. */
int shelf_unpack(int i) {
    char dir[96], opts[112];
    staged(dir, sizeof dir, i, "");
    mkdir(dir, 0755);
    snprintf(opts, sizeof opts, "-o -qq -d %s", dir);
    return unzip(i, opts, "") ? SH_CARD : SH_OK;
}

static void app_file(char *out, unsigned n, int i, const char *ext) {
    snprintf(out, n, SH_DIR "/apps/%s%s", apps[i].id, ext);
}

/* One staged file (rel, from the card's root) renamed over the card's, its folders made.
 * 0 when moved. */
static int move(int i, const char *rel) {
    char from[512], to[512];
    int at = (int)strlen(CARD "/"),
        a = snprintf(from, sizeof from, STAGING "/%s/%s", apps[i].id, rel),
        b = snprintf(to, sizeof to, CARD "/%s", rel);
    if (a < 0 || a >= (int)sizeof from || b < 0 || b >= (int)sizeof to) return 1;
    for (char *s = to + at; (s = strstr(s, "/")); *s++ = '/') *s = 0, mkdir(to, 0755);
    return rename(from, to) != 0;
}

/* Every staged file into place, the binary last (a rename: never half there), then the list of them
 * as .q2/apps/<id>.files. ponytail: a power cut part way leaves the files moved so far, the old tag
 * and list; a release that drops a file leaves the old one on the card and off the list. */
int shelf_place(int i) {
    char cmd[256], list[96], files[96];
    unsigned n = 0;
    int err = 0;
    staged(list, sizeof list, i, ".files");
    app_file(files, sizeof files, i, ".files");
    snprintf(cmd, sizeof cmd, "cd " STAGING "/%s && find %s -type f > %s", apps[i].id, apps[i].dir,
             list);
    char *rel = system(cmd) ? 0 : lines(list, &n);                /* FAT names hold no newline */
    for (int last = 0; rel && last < 2 && !(last && err); ++last) /* no new binary by a part set */
        for (const char *p = rel; p < rel + n; p += strlen(p) + 1) {
            int bin = *apps[i].bin && !strcmp(p, apps[i].bin);
            if (bin == last) err |= move(i, p);
        }
    free(rel);
    err = !rel || !n || err || rename(list, files);
    system("sync");
    return err ? SH_CARD : SH_OK;
}

/* Remove: app i's listed files, but those another app lists too (two fonts are both Rockbox's and
 * the themes'), then the folders that left empty, up to and with its own; then its list and tag. */
void shelf_remove(int i) {
    char path[512];
    unsigned n = 0, m[SH_MAX] = { 0 };
    char *other[SH_MAX] = { 0 };
    for (int j = 0; j < shelf_n; ++j)
        if (j != i) app_file(path, sizeof path, j, ".files"), other[j] = lines(path, &m[j]);
    if (*apps[i].bin) { /* first: a part remove still boots Q2 Pod */
        snprintf(path, sizeof path, CARD "/%s", apps[i].bin);
        unlink(path);
    }
    app_file(path, sizeof path, i, ".files");
    char *list = lines(path, &n);
    for (const char *p = list; p && p < list + n; p += strlen(p) + 1) {
        int shared = 0;
        for (int j = 0; j < shelf_n; ++j) shared |= shelf_listed(other[j], m[j], p);
        snprintf(path, sizeof path, CARD "/%s", p);
        if (*p && !shared) unlink(path);
        for (char *s; (s = strrchr(path, '/')) > path + strlen(CARD);) {
            *s = 0;
            if (rmdir(path)) break; /* not empty: nor is any folder above */
        }
    }
    free(list);
    for (int j = 0; j < shelf_n; ++j) free(other[j]);
    app_file(path, sizeof path, i, ".files");
    unlink(path);
    app_file(path, sizeof path, i, ".tag");
    unlink(path);
}

#ifndef PEQ_HOST
#include "offsets.inc"
#include "stock.h"

#define NU_REPO "https://github.com/" Q2POD_REPO "/releases/latest" /* tools/build.py --update-repo */
#if IPOD /* tools/release.py ASSETS */
#define NU_ASSET "update.tar"
#else
#define NU_ASSET "update-legacy.tar"
#endif
#define NU_FILE "/mnt/mmc/update.tar"
#define NU_CA "/etc/scrobble-ca.pem" /* scrobble.c's; github.com's and its asset host's roots */

enum { NU_IDLE, NU_RUNNING, NU_DONE, NU_LATEST, NU_NET, NU_MISSING, NU_CARD, NU_CANCELLED };

static struct {
    unsigned long thread;
    volatile int state, cancel, percent; /* cancel, percent: progress()'s pair */
    void *label; /* the row's label while System Update is open, for the percentage */
} nu __attribute__((section(".scratch")));

extern int stock_fwupdate_trampoline(void *win, void *ctx);

static unsigned save(const void *data, unsigned size, unsigned n, void *file) {
    return fwrite(data, size, n, file) * size;
}

/* xfer: a cancel flag followed by a percentage, nu's or the Apps page's. */
static int progress(void *xfer, long long total, long long now, long long c, long long d) {
    volatile int *x = xfer;
    if (total >= 1024)
        x[1] = (unsigned)(now >> 10) * 100 / (unsigned)(total >> 10); /* KB: 32-bit math */
    return x[0];
}

/* One transfer: the latest tag (url without asset, headers only, no redirect) or the asset to file. */
static int fetch(const char *url, void *file, char *tag, unsigned n, long *code,
                 volatile int *xfer) {
    void *c = curl_easy_init();
    int err = 1;
    char *location = 0;
    if (!c) return 1;
    curl_easy_setopt(c, 10002, url);          /* CURLOPT_URL */
    curl_easy_setopt(c, 10018, "Q2Pod");      /* USERAGENT */
    curl_easy_setopt(c, 99, 1L);              /* NOSIGNAL: off the UI thread */
    curl_easy_setopt(c, 78, 15L);             /* CONNECTTIMEOUT */
    curl_easy_setopt(c, 10065, NU_CA);        /* CAINFO */
    curl_easy_setopt(c, 64, 1L);              /* SSL_VERIFYPEER */
    curl_easy_setopt(c, 81, 2L);              /* SSL_VERIFYHOST */
    curl_easy_setopt(c, 20108, tls_untimed);  /* SSL_CTX_FUNCTION: no date checks */
    if (file) {
        curl_easy_setopt(c, 52, 1L);          /* FOLLOWLOCATION: to the asset host */
        curl_easy_setopt(c, 20011, save);     /* WRITEFUNCTION */
        curl_easy_setopt(c, 10001, file);     /* WRITEDATA */
        curl_easy_setopt(c, 19, 1024L);       /* LOW_SPEED_LIMIT: a stalled transfer ends */
        curl_easy_setopt(c, 20, 30L);         /* LOW_SPEED_TIME */
        curl_easy_setopt(c, 20219, progress); /* XFERINFOFUNCTION */
        curl_easy_setopt(c, 10057, xfer);     /* XFERINFODATA */
        curl_easy_setopt(c, 43, 0L);          /* NOPROGRESS off, for it */
    } else {
        curl_easy_setopt(c, 44, 1L);          /* NOBODY */
        curl_easy_setopt(c, 13, 30L);         /* TIMEOUT */
    }
    /* curl itself fails a body shorter than its Content-Length (PARTIAL_FILE). */
    err = curl_easy_perform(c) || curl_easy_getinfo(c, 0x200002, code); /* RESPONSE_CODE */
    if (!err && tag && !curl_easy_getinfo(c, 0x10001f, &location)) {    /* REDIRECT_URL */
        const char *t = netupdate_tag(location);
        tk_snprintf(tag, n, "%s", t ? t : "");
    }
    curl_easy_cleanup(c);
    return err;
}

static void *worker(void *unused) {
    char tag[64] = "";
    long code = 0;
    if (fetch(NU_REPO, 0, tag, sizeof tag, &code, &nu.cancel) || code != 302 || !*tag) {
        nu.state = NU_NET;
        return 0;
    }
    /* Tags are single-digit x.y.z, so strcmp orders them: nothing older than this build, which
     * stock's updater would flash. Q2POD_TAG is empty in --dev builds, which take any tag. */
    if (*Q2POD_TAG && strcmp(tag, Q2POD_TAG) <= 0) {
        nu.state = NU_LATEST;
        return 0;
    }
    /* The old update.tar stays until the new one is whole. ponytail: needs the tar's ~55 MB free
     * beside it; TLS and curl's length check are the only integrity checks before stock's own MD5s. */
    if (nu.cancel) { /* pressed while checking, or the page was left */
        nu.state = NU_CANCELLED;
        return 0;
    }
    void *f = fopen(NU_FILE ".part", "wb");
    int err = !f, state = NU_CARD;
    if (f) {
        err = fetch(NU_REPO "/download/" NU_ASSET, f, 0, 0, &code, &nu.cancel);
        state = nu.cancel ? NU_CANCELLED : err ? (ferror(f) ? NU_CARD : NU_NET) : code != 200 ? NU_MISSING : NU_CARD;
        err = err || code != 200 || fflush(f) || fsync(fileno(f)) || ferror(f);
        err = fclose(f) || err;
    }
    if (!err && !nu.cancel && !rename(NU_FILE ".part", NU_FILE)) { /* a late cancel still cancels */
        void *dir = fopen("/mnt/mmc", "r"); /* the rename's directory entry, before stock flashes */
        if (dir) fsync(fileno(dir)), fclose(dir);
        state = NU_DONE;
    } else
        unlink(NU_FILE ".part");
    nu.state = state;
    return 0;
}

static void show(const char *text) {
    if (nu.label) widget_set_text_utf8(nu.label, text);
}

static int wait_done(const void *unused) {
    static const char *const said[] = {
        [NU_LATEST] = "Latest firmware installed", [NU_NET] = "Can't reach GitHub",
        [NU_MISSING] = "No update for this build", [NU_CARD] = "Can't write to the card",
        [NU_CANCELLED] = "Download cancelled",
    };
    char s[32];
    if (nu.state == NU_RUNNING) {
        reset_poweroptions_timer(1, 1, 0); /* standby, auto power-off; not the screen's */
        tk_snprintf(s, sizeof s, "Downloading %d%%", nu.percent);
        show(nu.cancel ? "Cancelling…" : nu.percent ? s : "Checking…");
        return 8; /* RET_REPEAT */
    }
    pthread_join(nu.thread, 0);
    int state = nu.state;
    nu.state = NU_IDLE;
    if (nu.label) widget_set_tr_text(nu.label, "update_online");
    if (state != NU_DONE)
        toast(said[state]);
    else if (nu.label)
        fwupdate_tf(); /* stock's TF card update: its battery check and confirm */
    else
        toast("Update downloaded: use TF card update");
    return 0;
}

/* A second press cancels: the transfer ends at its next progress call. */
static int online_click(void *ctx, void *event) {
    if (nu.state == NU_RUNNING) {
        nu.cancel = 1;
        show("Cancelling…");
    } else if (nu.state != NU_IDLE) {
        /* ended, not yet joined by wait_done */
    } else if (get_wifisignal() <= 0) {
        toast("Connect to Wi-Fi first");
    } else {
        nu.cancel = nu.percent = 0;
        nu.state = NU_RUNNING;
        if (pthread_create(&nu.thread, 0, worker, 0)) {
            nu.state = NU_IDLE;
            toast("Can't start the download");
        } else {
            show("Checking…");
            timer_add(wait_done, (void *)0, 500);
        }
    }
    return 0;
}

/* Leaving the page cancels: the card may be unmounted or shared over USB next. */
static int label_gone(void *ctx, void *event) {
    if (nu.label == ctx) nu.label = 0, nu.cancel = 1;
    return 0;
}

/* systemset_fwupdate_page_init: stock's two rows (0x4c3140), each button named by its index, which
 * stock's click (0x4c2fac) reads with atoi: 0 TF card update, 1 Shanling's server. Renamed "2", the
 * second does nothing in stock's click and ours runs. */
int ringnav_fwupdate(void *win, void *ctx) {
    int result = stock_fwupdate_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_fwupdate", 1) : (void *)0;
    void *button = view && widget_count_children(view) == 2 ? widget_get_child(widget_get_child(view, 1), 0) : 0;
    if (button) {
        widget_set_name(button, "2");
        widget_on(button, EVT_CLICK, online_click, 0);
        nu.label = widget_get_child(button, 0);
        widget_on(nu.label, EVT_DESTROY, label_gone, nu.label);
        if (nu.state == NU_RUNNING) show("Downloading…");
    }
    return result;
}

/* The Apps page (System settings): a row per app in sources.list, its state as the row's detail.
 * One job at a time on one thread: the page's opening check (the list, then the latest tags), an
 * install, a remove or Spotify's erase. Leaving the page leaves it running; its progress shows when
 * the page opens again. */
#define SH_JSON "/tmp/q2-shelf.json"
#define SH_LIST SH_DIR "/sources.list" /* the last list fetched, for the page offline */
#define SH_LIST_URL "https://raw.githubusercontent.com/DiamondBond/q2-pod/main/sources.list"
#define SH_RESERVE (64ull << 20) /* left free on the card beyond a download or what it unpacks */
#define SH_STALE_MS (10 * 60 * 1000ull)
enum { JOB_CHECK = 1, JOB_INSTALL, JOB_REMOVE, JOB_ERASE };
enum { ST_INSTALL, ST_UPDATE, ST_INSTALLED };
enum { ROW_ERASE = SH_MAX, ROW_NONE }; /* map's rows that are no app: Erase, and no list yet */

static struct {
    unsigned long thread;
    volatile int running, cancel, percent, stage, result; /* cancel, percent: progress()'s pair */
    int job, app, armed, rows, n, map[SH_MAX + 1];        /* map: a row's app or ROW_ */
    unsigned need;                                        /* SH_SPACE's MB */
    unsigned long long checked;                           /* now_ms of the last whole check */
    shelf_app next[SH_MAX]; /* the check's list (n apps), the page's once the check ends */
    void *page, *row[SH_MAX + 1];
} sh __attribute__((section(".scratch")));

static int is(int i, const char *id) { return i >= 0 && i < shelf_n && !strcmp(apps[i].id, id); }

static int sh_find(const char *id) {
    for (int i = 0; i < shelf_n; ++i)
        if (is(i, id)) return i;
    return -1;
}

/* The folder (".<id>") an install or remove is replacing, else 0: addon.c starts no app there. */
const char *shelf_busy(void) {
    return sh.running && sh.job != JOB_CHECK && sh.job != JOB_ERASE ? apps[sh.app].dir : 0;
}

/* glibc 2.28 MIPS o32 stat v3: st_mode at byte 20; 0 when path is missing. */
static unsigned file_mode(const char *path) {
    unsigned info[36];
    return __xstat(3, path, info) ? 0 : info[5] & 0170000;
}

/* url's body (1 MB at most) through SH_JSON, NUL-terminated; 0 unless a whole 200. */
static char *get(const char *url, long *code) {
    unsigned n = 0;
    void *f = fopen(SH_JSON, "wb");
    int err = !f || fetch(url, f, 0, 0, code, &sh.cancel);
    err = (f && fclose(f)) || err;
    char *body = err || *code != 200 ? 0 : read_all(SH_JSON, 1u << 20, &n);
    unlink(SH_JSON);
    sh.percent = 0;
    return body;
}

/* The release's tag (also a->latest), its zip's URL and size. */
static int release(shelf_app *a, char *tag, char *url, unsigned *size) {
    char api[160];
    long code = 0;
    snprintf(api, sizeof api, "https://api.github.com/repos/%s/releases/latest", a->repo);
    char *json = get(api, &code);
    int r = !json ? SH_NET : shelf_release(json, tag, 32, url, 256, size) ? SH_OK : SH_MISSING;
    if (code == 404) r = SH_MISSING;             /* no release */
    if (code == 403 || code == 429) r = SH_BUSY; /* 60 unauthenticated requests an hour */
    free(json);
    if (!r) snprintf(a->latest, sizeof a->latest, "%s", tag);
    return r;
}

/* The opening check: the list fetched into sh.next, kept as SH_LIST with any app it dropped that is
 * still installed (so it can be removed), else the page's own; then each one's latest release.
 * Whether every release was checked: a list that failed is fetched again with the next check. */
static int check(void) {
    char path[96], tag[32], url[256], text[SH_MAX * 120];
    unsigned size, m = 0;
    long code = 0;
    char *list = get(SH_LIST_URL, &code);
    int n = list ? shelf_sources(list, sh.next, SH_MAX) : 0, fresh = n > 0, ok = 1;
    free(list);
    if (!n) memcpy(sh.next, apps, sizeof apps), n = shelf_n; /* a bad list never replaces one */
    for (int i = 0; fresh && i < shelf_n && n < SH_MAX; ++i) {
        int listed = 0;
        for (int k = 0; k < n; ++k) listed |= !strcmp(sh.next[k].id, apps[i].id);
        app_file(path, sizeof path, i, ".tag");
        if (!listed && file_mode(path)) sh.next[n++] = apps[i];
    }
    for (int k = 0; fresh && k < n; ++k)
        m += snprintf(text + m, sizeof text - m, "%s %s\n", sh.next[k].repo, sh.next[k].name);
    if (fresh) mkdir(SH_DIR, 0755), blob_io(SH_LIST, SH_LIST ".tmp", text, m, 1);
    sh.n = n;
    for (int k = 0; k < n && !sh.cancel; ++k) ok &= !release(&sh.next[k], tag, url, &size);
    return ok;
}

/* SH_OK when the card has bytes and SH_RESERVE free; else SH_SPACE and sh.need the MB short. */
static int room(unsigned bytes) {
    unsigned fs[32] = { 0 }; /* statfs, MIPS o32: f_bsize word 1, f_bavail word 7 */
    if (statfs(CARD, fs)) return SH_CARD;
    unsigned long long have = (unsigned long long)fs[7] * fs[1], want = bytes + SH_RESERVE;
    if (have >= want) return SH_OK;
    sh.need = (unsigned)((want - have + (1u << 20) - 1) >> 20);
    return SH_SPACE;
}

/* Cancel lands during the download, else after the step it was pressed in; never once moving
 * starts. ponytail: TLS and curl's length check are the only checks on the zip; unzip's CRCs the
 * only ones in it. */
static int install(int i) {
    char tag[32], url[256], part[96], path[96];
    unsigned size = 0, total = 0;
    long code = 0;
    int r = release(&apps[i], tag, url, &size);
    if (sh.cancel) return SH_CANCELLED;
    if (r) return r;
    system("rm -rf " STAGING); /* an interrupted install's, too */
    mkdir(SH_DIR, 0755), mkdir(STAGING, 0755), mkdir(SH_DIR "/apps", 0755);
    if ((r = room(size))) return r;
    staged(part, sizeof part, i, ".part");
    void *f = fopen(part, "wb");
    if (!f) return SH_CARD;
    sh.stage = 1;
    int err = fetch(url, f, 0, 0, &code, &sh.cancel), bad = ferror(f);
    bad = fclose(f) || bad;
    r = sh.cancel ? SH_CANCELLED : bad ? SH_CARD : err ? SH_NET : code != 200 ? SH_MISSING : SH_OK;
    if (!r) sh.stage = 2, r = shelf_list(i, &total);
    if (!r) r = room(total);
    if (!r) r = shelf_unpack(i);
    if (!r && sh.cancel) r = SH_CANCELLED;
    if (!r) {
        sh.stage = 3;
        if (is(i, "spotify"))
            system(SPOT_KILL);   /* librespot runs from .spotify, if started since */
        addon_quit(apps[i].dir); /* a card app, the same: its files closed first */
        app_file(path, sizeof path, i, ".tag");
        r = shelf_place(i);
        char tmp[100];
        snprintf(tmp, sizeof tmp, "%s.tmp", path);
        if (!r && !blob_io(path, tmp, tag, strlen(tag), 1)) r = SH_CARD;
    }
    system("rm -rf " STAGING);
    return r;
}

static void *sh_worker(void *unused) {
    int i = sh.app, t = sh_find("themes");
    if (sh.job == JOB_CHECK) {
        if (check() && !sh.cancel) sh.checked = now_ms();
    } else if (sh.job == JOB_INSTALL)
        sh.result = install(i);
    else {
        if (sh.job == JOB_ERASE)
            system("rm -rf " SPOT_DIR "/cache " SPOT_DIR "/.cache-size"), rmdir(SPOT_DIR);
        else if (is(i, "rockbox") && t >= 0)
            shelf_remove(t); /* no themes without Rockbox */
        if (sh.job == JOB_REMOVE) addon_quit(apps[i].dir), shelf_remove(i);
        system("sync");
    }
    sh.running = 0;
    return 0;
}

/* ST_INSTALLED with a tag file (unless the latest differs), ST_UPDATE for a manual install. */
static int sh_state(int i) {
    char path[96], tag[40];
    app_file(path, sizeof path, i, ".tag");
    state_read(path, tag, sizeof tag); /* "\n" and the tag */
    if (tag[1])
        return *apps[i].latest && strcmp(tag + 1, apps[i].latest) ? ST_UPDATE : ST_INSTALLED;
    if (*apps[i].bin) {
        snprintf(path, sizeof path, CARD "/%s", apps[i].bin);
        if (file_mode(path) == 0100000) return ST_UPDATE;
    }
    return ST_INSTALL;
}

static void sh_text(int r) {
    static const char *const stages[] = { "Checking…", "Downloading", "Unpacking…", "Installing…",
                                          "Removing…" };
    static const char *const states[] = { "Install", "Update", "Installed" };
    int i = sh.map[r], armed = sh.armed == r + 1,
        busy = (sh.job == JOB_INSTALL || sh.job == JOB_REMOVE) && sh.app == i;
    char s[40];
    const char *t = s;
    if (i == ROW_NONE)
        t = sh.job == JOB_CHECK ? "Loading…" : "Connect to Wi-Fi to load the list";
    else if (i == ROW_ERASE && sh.job == JOB_ERASE)
        t = "Erasing…";
    else if (i == ROW_ERASE)
        t = armed ? "Press again to erase the login and cache" : "Login and cache";
    else if (busy && sh.cancel)
        t = "Cancelling…";
    else if (busy && sh.stage == 1 && sh.percent)
        snprintf(s, sizeof s, "Downloading %d%%", sh.percent);
    else if (busy)
        t = stages[sh.stage];
    else if (armed && sh_state(i) == ST_INSTALLED)
        t = "Press again to remove";
    else if (armed) /* docs/boot.md#rockbox */
        t = "Starts at power-on; Boot to changes that. Press again to install";
    else
        t = states[sh_state(i)];
    widget_set_text_utf8(widget_get_child(sh.row[r], 1), t);
}

static void sh_rows(void);
static void sh_refresh(void) {
    for (int r = 0; sh.page && r < sh.rows; ++r) sh_text(r);
}

static int sh_wait(const void *unused) {
    static const char *const said[] = {
        [SH_NET] = "Can't reach GitHub",           [SH_CARD] = "Can't write to the card",
        [SH_MISSING] = "No update for this build", [SH_BAD] = "The download is not a usable app",
        [SH_CANCELLED] = "Download cancelled",     [SH_BUSY] = "GitHub is busy, try later",
    };
    char s[64];
    if (sh.running) {
        if (sh.job != JOB_CHECK) reset_poweroptions_timer(1, 1, 0); /* standby, auto power-off */
        sh_refresh();
        return 8; /* RET_REPEAT */
    }
    pthread_join(sh.thread, 0);
    int job = sh.job, r = sh.result, i = sh.app;
    sh.job = 0;
    if (job == JOB_CHECK) /* the rows built again: the list may have changed */
        memcpy(apps, sh.next, sizeof apps), shelf_n = sh.n, sh.rows = -1;
    if (job != JOB_CHECK && is(i, "spotify")) spot_stop(); /* spotify.c forgets the old one */
    sh_rows();
    if (job == JOB_INSTALL && r == SH_SPACE)
        snprintf(s, sizeof s, "Needs %u MB more space", sh.need), toast(s);
    else if (job == JOB_INSTALL && r)
        toast(said[r]);
    else if (job == JOB_INSTALL && is(i, "rockbox"))
        toast(IPOD ? "Rockbox is ready. Find it on Home"
                   : "Rockbox is ready. It starts at power-on");
    else if (job == JOB_INSTALL && is(i, "themes"))
        toast("Rockbox Themes are ready in Rockbox's Theme Settings");
    else if (job == JOB_INSTALL)
        snprintf(s, sizeof s, "%s is ready in Streaming", apps[i].name), toast(s);
    else if (job == JOB_REMOVE)
        snprintf(s, sizeof s, "%s removed", apps[i].name), toast(s);
    else if (job == JOB_ERASE)
        toast("Spotify's login and cache erased");
    return 0;
}

static void sh_start(int job, int i) {
    if (is(i, "spotify") && job != JOB_CHECK)
        spot_stop(); /* on this thread: spotify.c's state too */
    if (job != JOB_CHECK) addon_forget(apps[i].dir);
    sh.job = job, sh.app = i, sh.cancel = sh.percent = sh.result = 0, sh.running = 1;
    sh.stage = job == JOB_REMOVE ? 4 : 0;
    if (pthread_create(&sh.thread, 0, sh_worker, 0)) {
        sh.job = sh.running = 0;
        toast("Can't start the download");
    } else
        timer_add(sh_wait, (void *)0, 500);
}

/* Install and Update at once; Installed's Remove, Rockbox's first Install (it becomes the power-on
 * default) and Erase on a second press of the same row. Pressed again, the running install cancels.
 * With no list yet, the one row loads it. */
static int sh_click(void *ctx, void *event) {
    int r = (int)(long)ctx, i = sh.map[r], armed = sh.armed == r + 1;
    sh.armed = 0;
    if (i == ROW_NONE && sh.job) {
        /* loading it already */
    } else if (sh.job == JOB_CHECK) { /* needs no answer: stopped, so the next press acts */
        sh.cancel = 1;
        toast("Stopped checking for updates");
    } else if (sh.job) {
        if (sh.job == JOB_INSTALL && sh.app == i && sh.stage < 3)
            sh.cancel = 1; /* not once moving */
        else if (sh.job != JOB_INSTALL || sh.app != i)
            toast("Wait for the one in progress");
    } else if (i != ROW_NONE && (i == ROW_ERASE || sh_state(i) == ST_INSTALLED)) {
        if (!armed)
            sh.armed = r + 1;
        else
            sh_start(i == ROW_ERASE ? JOB_ERASE : JOB_REMOVE,
                     i == ROW_ERASE ? sh_find("spotify") : i);
    } else if (get_wifisignal() <= 0)
        toast("Connect to Wi-Fi first");
    else if (i == ROW_NONE)
        sh_start(JOB_CHECK, 0);
    else if (is(i, "themes") && !rockbox_available())
        toast("Install Rockbox first");
    else if (is(i, "rockbox") && sh_state(i) == ST_INSTALL && !armed)
        sh.armed = r + 1;
    else
        sh_start(JOB_INSTALL, i);
    sh_refresh();
    return 0;
}

static int sh_closed(void *ctx, void *event) {
    sh.page = 0, sh.rows = sh.armed = 0;
    return 0;
}

/* The rows, in the list's order: Rockbox Themes only with Rockbox on the card, Erase after Spotify
 * only with a .spotify folder, one to load the list while there is none; built again when a job
 * ends changes which. */
static void sh_rows(void) {
    int map[SH_MAX + 1] = { 0 }, n = 0;
    for (int i = 0; i < shelf_n; ++i) {
        if (is(i, "themes") && !rockbox_available()) continue;
        map[n++] = i;
        if (is(i, "spotify") && file_mode(SPOT_DIR) == 0040000) map[n++] = ROW_ERASE;
    }
    if (!n) map[n++] = ROW_NONE;
    if (sh.page && (n != sh.rows || memcmp(map, sh.map, sizeof map))) {
        memcpy(sh.map, map, sizeof map);
        sh.rows = 0;
        void *view = page_list(sh.page, sh.page, 0, "App Store", n, 64);
        for (int r = 0; r < n; ++r)
            sh.row[r] = page_row_detail(view, r,
                                        map[r] == ROW_ERASE  ? "Erase Spotify Data"
                                        : map[r] == ROW_NONE ? "App list"
                                                             : apps[map[r]].name,
                                        "", sh_click);
        sh.rows = n;
    }
    sh_refresh();
}

/* System settings' Apps row. The card's last list, until a check fetches the list again: with Wi-Fi
 * when the last whole check is over SH_STALE_MS old (or none), as GitHub allows 60 unauthenticated
 * API requests an hour. */
int shelf_open(void *ctx, void *event) {
    if (sh.page || !(sh.page = page_open("apps_page", sh_closed, page_keyup))) return 0;
    if (!sh.job && !shelf_n) {
        unsigned n = 0;
        char *list = read_all(SH_LIST, 1u << 16, &n);
        shelf_n = shelf_sources(list, apps, SH_MAX);
        free(list);
    }
    sh_rows();
    if (!sh.job && get_wifisignal() > 0 && (!sh.checked || now_ms() - sh.checked > SH_STALE_MS))
        sh_start(JOB_CHECK, 0), sh_refresh();
    return 0;
}
#endif
