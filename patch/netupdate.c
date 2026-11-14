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
 * power-on runs Rockbox's. dropped: gone from the list but installed, so it can only be removed. */
typedef struct {
    char name[32], id[16], repo[80], dir[17], bin[40], latest[32], dropped;
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
/* The firmware's own card folders, never a card app's: a remove deletes .<id> whole. */
static const char *const reserved[] = { "books", "coverflow", "downpic", "photos", "q2", "sldp",
                                        "tidal" };

/* Whether id is a special[] app's, which a remove never deletes whole. */
static int special_id(const char *id) {
    for (unsigned k = 0; k < sizeof special / sizeof *special; ++k)
        if (!strcmp(special[k].id, id)) return 1;
    return 0;
}

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

/* sources.list's lines, "owner/repo [Name]" ("#" a comment, "-" before it a dropped app), into
 * out: at most max, each owner, repo and id checked (they go into URLs and shell commands; the id
 * as addon.c's, 1 to 15 of a-z0-9-), a bad or repeated id's line skipped. The count. */
int shelf_sources(const char *text, shelf_app *out, int max) {
    int n = 0;
    for (const char *p = text, *e; p && *p && n < max; p = *e ? e + 1 : e) {
        e = strstr(p, "\n");
        e = e ? e : p + strlen(p);
        while (p < e && (*p == ' ' || *p == '\t')) ++p;
        int dropped = p < e && *p == '-';
        p += dropped;
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
        shelf_app a = { .dropped = (char)dropped };
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
            for (unsigned k = 0; k < sizeof reserved / sizeof *reserved; ++k)
                bad |= strlen(reserved[k]) == in && !memcmp(reserved[k], id, in);
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

/* GitHub's release JSON: the asset called name, its size and its SHA-256 as 64 hex digits in sha
 * (65 bytes), from the asset's own "size" and "digest": "sha256:…", GitHub's record of the bytes
 * uploaded. 0 when any is missing or not before the asset's last key, so never the next asset's:
 * a release without digests (uploads before June 2025 have none) is not installed. */
int netupdate_asset(const char *json, const char *name, unsigned *size, char *sha) {
    char s[128];
    for (const char *p = json_at(json, "\"assets\""); (p = json_at(p, "\"name\""));) {
        if (!json_string(p, s, sizeof s) || strcmp(s, name)) continue;
        const char *end = strstr(p, "\"browser_download_url\""), *z = json_at(p, "\"size\""),
                   *d = json_at(p, "\"digest\"");
        if (!end || !z || z > end || !d || d > end || !json_string(d, s, sizeof s)) return 0;
        if (strlen(s) != 71 || memcmp(s, "sha256:", 7)) return 0;
        for (int i = 7; i < 71; ++i)
            if (!((s[i] >= '0' && s[i] <= '9') || (s[i] >= 'a' && s[i] <= 'f'))) return 0;
        memcpy(sha, s + 7, 65);
        *size = (unsigned)atoi(z);
        return *size != 0;
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
 * the themes'), then the folders that left empty, up to and with its own; then its list and tag.
 * A card app's folder goes whole, with what the app saved there (a login among it). */
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
    /* dir is shelf_sources' checked ".<id>". ponytail: addon_quit waits QUIT_MS (20 s), so an app
     * still saving on its TERM past that can write files back. */
    if (!special_id(apps[i].id)) {
        snprintf(path, sizeof path, "rm -rf '" CARD "/%s'", apps[i].dir);
        system(path);
    }
    app_file(path, sizeof path, i, ".files");
    unlink(path);
    app_file(path, sizeof path, i, ".tag");
    unlink(path);
}

#ifndef PEQ_HOST
#include "offsets.inc"
#include "stock.h"

/* path's SHA-256 as 64 hex digits in hex (65 bytes) and its bytes in *size, read back in SHA_CHUNK
 * pieces; 0 when it can't be read whole. */
#define SHA_CHUNK (1u << 14)
int netupdate_sha256(const char *path, char *hex, unsigned *size) {
    unsigned char *buf = calloc(1, SHA_CHUNK), digest[32] = { 0 };
    void *ctx = calloc(1, 128), *f = buf && ctx ? fopen(path, "rb") : 0; /* stock's sha256_t, 0x68 bytes */
    unsigned n = SHA_CHUNK, total = 0;
    if (f) tk_sha256_init(ctx);
    while (f && n == SHA_CHUNK) {
        total += n = fread(buf, 1, SHA_CHUNK, f);
        tk_sha256_hash(ctx, buf, n);
    }
    int ok = f && !ferror(f);
    if (f) fclose(f), tk_sha256_done(ctx, digest);
    free(buf);
    free(ctx);
    for (int i = 0; i < 64; ++i) hex[i] = "0123456789abcdef"[digest[i / 2] >> (i & 1 ? 0 : 4) & 15];
    hex[64] = 0;
    *size = total;
    return ok;
}

#define NU_REPO "https://github.com/" Q2POD_REPO "/releases/latest" /* tools/build.py --update-repo */
#define NU_API "https://api.github.com/repos/" Q2POD_REPO "/releases/tags/"
#define NU_GET "https://github.com/" Q2POD_REPO "/releases/download/"
#define NU_ASSET "update.tar"
#define NU_FILE "/mnt/mmc/update.tar"
#define NU_JSON "/tmp/q2-update.json"
#define NU_CA "/etc/scrobble-ca.pem" /* scrobble.c's; github.com's and its asset host's roots */
#define NU_BATTERY 16 /* percent: under it, off a charger, stock's updater refuses to flash */
#define NU_TRIES 3    /* tries in a row that add nothing to the part, before a press gives up */
#define SH_RESERVE (64ull << 20) /* left free on the card beyond a download or what it unpacks */

enum { NU_IDLE, NU_RUNNING, NU_DONE, NU_LATEST, NU_NET, NU_MISSING, NU_CARD, NU_CANCELLED, NU_BUSY,
       NU_SPACE, NU_BAD };

static struct {
    unsigned long thread;
    volatile int state, cancel, percent; /* cancel, percent: progress()'s pair */
    volatile int stage;    /* for the label: 1 the tar is coming, 2 it is read back, 3 between tries */
    volatile int base;     /* the percentage the part held when this try began */
    unsigned need;         /* NU_SPACE's MB */
    void *label; /* the row's label while System Update is open, for the percentage */
} nu __attribute__((section(".scratch")));

extern int stock_fwupdate_trampoline(void *win, void *ctx);

/* The notice (netupdate_poll): with Wi-Fi on, now and then the latest release's tag, kept when it is
 * newer than this build; Home tells of it once a release and System Update's row names it. */
#define NU_TOLD "/mnt/data/q2-update.told" /* the tag Home last told of */
#define NU_POLL_MS 2000u
#define NU_EVERY_MS (24u * 60 * 60 * 1000) /* between checks that reached GitHub */
#define NU_AGAIN_MS (60u * 60 * 1000)      /* after one that didn't */

static struct {
    unsigned at, polled; /* time_now_ms of the last check and the last poll; 0 none */
    volatile int busy;   /* the check's thread runs: tag is its */
    int ok, read;        /* the last check reached GitHub; told is NU_TOLD's */
    char tag[16], told[16];
} nc __attribute__((section(".scratch")));

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

/* One transfer: the latest tag (url without asset, headers only, no redirect) or the asset to file,
 * from its byte `from` on (HTTP Range) when the file holds those before it. curl's own code. */
static int fetch(const char *url, void *file, char *tag, unsigned n, long *code,
                 volatile int *xfer, long from) {
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
        if (from) { /* RESUME_FROM: curl fails a reply that isn't the range */
            curl_easy_setopt(c, 21, from);
            curl_easy_setopt(c, 45, 1L); /* FAILONERROR: a 403/429/5xx keeps the part, not its error page */
        }
    } else {
        curl_easy_setopt(c, 44, 1L);          /* NOBODY */
        curl_easy_setopt(c, 13, 30L);         /* TIMEOUT */
    }
    /* curl itself fails a body shorter than its Content-Length (PARTIAL_FILE). */
    err = curl_easy_perform(c);
    if (!err) err = curl_easy_getinfo(c, 0x200002, code); /* RESPONSE_CODE */
    if (!err && tag && !curl_easy_getinfo(c, 0x10001f, &location)) {    /* REDIRECT_URL */
        const char *t = netupdate_tag(location);
        tk_snprintf(tag, n, "%s", t ? t : "");
    }
    curl_easy_cleanup(c);
    return err;
}

/* url's body (1 MB at most) through the file tmp, NUL-terminated; 0 unless a whole 200. */
static char *get_by(const char *url, long *code, const char *tmp, volatile int *xfer) {
    unsigned n = 0;
    void *f = fopen(tmp, "wb");
    int err = !f || fetch(url, f, 0, 0, code, xfer, 0);
    err = (f && fclose(f)) || err;
    char *body = err || *code != 200 ? 0 : read_all(tmp, 1u << 20, &n);
    unlink(tmp);
    xfer[1] = 0;
    return body;
}

/* SH_OK when the card has bytes and SH_RESERVE free; else SH_SPACE and *need the MB short. */
static int room(unsigned long long bytes, unsigned *need) {
    unsigned fs[32] = { 0 }; /* statfs, MIPS o32: f_bsize word 1, f_bavail word 7 */
    if (statfs(CARD, fs)) return SH_CARD;
    unsigned long long have = (unsigned long long)fs[7] * fs[1], want = bytes + SH_RESERVE;
    if (have >= want) return SH_OK;
    *need = (unsigned)((want - have + (1u << 20) - 1) >> 20);
    return SH_SPACE;
}

/* path's bytes; -1 when it can't be opened. */
static long file_bytes(const char *path) {
    void *f = fopen(path, "rb");
    long n = f && !fseek(f, 0, 2) ? ftell(f) : -1;
    if (f) fclose(f);
    return n;
}

/* A press's work, to the state it ends in. The old update.tar stays until the new one is whole:
 * only a part of the release's size and SHA-256, as the card gives it back, is renamed over it. */
static int update(void) {
    char tag[64] = "", url[256], want[65], got[65];
    unsigned size = 0, n = 0;
    long code = 0;
    if (fetch(NU_REPO, 0, tag, sizeof tag, &code, &nu.cancel, 0) || code != 302 || !*tag)
        return NU_NET;
    /* Tags are single-digit x.y.z, so strcmp orders them: nothing older than this build, which
     * stock's updater would flash. Q2POD_TAG is empty in --dev builds, which take any tag.
     * ponytail: a beta (BETA in tools/build.py) refuses its own version's release too, so its testers
     * install that from the card; take an equal tag under a beta if they shouldn't have to. */
    if (*Q2POD_TAG && strcmp(tag, Q2POD_TAG) <= 0) return NU_LATEST;
    if (nu.cancel) return NU_CANCELLED; /* pressed while checking, or the page was left */
    /* That tag's release, for what GitHub recorded of its update.tar; then the tar by the same tag,
     * so a release published meanwhile can't pair one's bytes with the other's digest. */
    snprintf(url, sizeof url, NU_API "%s", tag);
    char *json = get_by(url, &code, NU_JSON, &nu.cancel);
    int found = json != 0, listed = netupdate_asset(json, NU_ASSET, &size, want);
    free(json);
    if (nu.cancel) return NU_CANCELLED;
    if (!found) /* 60 unauthenticated API requests an hour */
        return code == 403 || code == 429 ? NU_BUSY : code == 404 ? NU_MISSING : NU_NET;
    if (!listed) return NU_MISSING;
    /* Room for the tar and for stock's unpacking of it beside it. ponytail: an old update.tar the
     * new one replaces isn't counted as room. */
    long held = file_bytes(NU_FILE ".part"); /* its bytes are already out of the free room */
    unsigned long long left = 2ull * size - (held < 0 || (unsigned long)held > size ? 0 : held);
    int r = card_mounted() ? room(left, &nu.need) : SH_CARD;
    if (r) return r == SH_SPACE ? NU_SPACE : NU_CARD;
    snprintf(url, sizeof url, NU_GET "%s/" NU_ASSET, tag);
    /* The tar, each try carrying on from what the part holds: a try before it in this press, or what
     * a lost connection, a power cut or leaving the page left on the card. A part already whole is
     * only read back. Whatever a part holds (an older release's start, a card's stale blocks), the
     * read-back alone decides. ponytail: a part of another release costs the rest of this one's
     * download before it is found damaged and deleted. */
    int state = NU_RUNNING, tries = 0;
    while (state == NU_RUNNING) {
        long have = file_bytes(NU_FILE ".part");
        if (have < 0 || (unsigned long)have > size) have = 0; /* none, or not this release's */
        if ((unsigned long)have == size) break;
        void *f = fopen(NU_FILE ".part", have ? "ab" : "wb");
        if (!f) return NU_CARD;
        nu.base = (int)((unsigned)(have >> 10) * 100 / ((size >> 10) + 1)); /* KB: 32-bit math */
        nu.percent = 0, nu.stage = 1;
        int err = fetch(url, f, 0, 0, &code, &nu.cancel, have), bad = ferror(f);
        bad = fflush(f) || fsync(fileno(f)) || bad; /* a part cut short too: it is carried on from */
        bad = fclose(f) || bad;
        if (nu.cancel)
            state = NU_CANCELLED;
        else if (bad)
            state = NU_CARD;
        else if (!err && code == (have ? 206 : 200))
            break;
        else if (!err && !have)
            state = NU_MISSING;
        else {
            /* A reply that wasn't the range asked for (curl's RANGE_ERROR, or no 206) left the
             * part unusable: the next try starts it again. */
            if (!err || err == 33) unlink(NU_FILE ".part");
            tries = file_bytes(NU_FILE ".part") > have ? 0 : tries + 1;
            nu.stage = 3;
            for (int i = 0; tries < NU_TRIES && i < 50 && !nu.cancel; ++i) usleep(100000); /* 5 s */
            if (tries == NU_TRIES || nu.cancel) state = nu.cancel ? NU_CANCELLED : NU_NET;
        }
    }
    if (state == NU_RUNNING) {
        nu.stage = 2;
        state = !netupdate_sha256(NU_FILE ".part", got, &n) ? NU_CARD
                : n != size || strcmp(got, want)            ? NU_BAD
                                                            : NU_DONE;
    }
    /* Read once, after the close and the check: a press while the card took the last bytes (the
     * fsync of the whole part, seconds on a slow card, the label at 100%) or gave them back is a
     * cancel too, not "Can't write". */
    if (nu.cancel) state = NU_CANCELLED;
    if (state == NU_DONE) {
        if (rename(NU_FILE ".part", NU_FILE)) state = NU_CARD;
        void *dir = fopen("/mnt/mmc", "r"); /* the rename's directory entry, before stock flashes */
        if (dir) fsync(fileno(dir)), fclose(dir);
    }
    /* Kept for the next press to carry on from: what the network left, and what leaving the page
     * did (nu.cancel 2). A press's own cancel, a card error and a damaged part delete it. */
    if (state != NU_DONE && state != NU_NET && nu.cancel != 2) unlink(NU_FILE ".part");
    return state;
}

static void *worker(void *unused) {
    nu.state = update();
    return 0;
}

static void show(const char *text) {
    if (nu.label) widget_set_text_utf8(nu.label, text);
}

/* The row at rest: the newer release a check found, else its own name. */
static void named(void) {
    char s[32];
    if (!nc.busy && *nc.tag)
        tk_snprintf(s, sizeof s, "升级到 %s", nc.tag), show(s);
    else if (nu.label)
        widget_set_tr_text(nu.label, "中文固件更新");
}

static int wait_done(const void *unused) {
    static const char *const said[] = {
        [NU_LATEST] = "已是最新版本", [NU_NET] = "无法连接 GitHub",
        [NU_MISSING] = "此版本暂无更新", [NU_CARD] = "无法写入存储卡",
        [NU_CANCELLED] = "已取消下载", [NU_BUSY] = "GitHub 繁忙，请稍后再试",
        [NU_BAD] = "下载已损坏，请重试",
    };
    char s[32];
    if (nu.state == NU_RUNNING) {
        reset_poweroptions_timer(1, 1, 0); /* standby, auto power-off; not the screen's */
        int percent = nu.base + nu.percent * (100 - nu.base) / 100; /* of the whole tar */
        tk_snprintf(s, sizeof s, "正在下载 %d%%", percent);
        show(nu.cancel       ? "正在取消…"
             : nu.stage == 3 ? "正在重连…"
             : nu.stage == 2 ? "正在校验…"
             : nu.stage && percent ? s
                                   : "检查中…");
        return 8; /* RET_REPEAT */
    }
    pthread_join(nu.thread, 0);
    int state = nu.state;
    nu.state = NU_IDLE;
    if (state == NU_LATEST && !nc.busy) *nc.tag = 0; /* a press's answer is the newer */
    named();
    if (state == NU_SPACE)
        tk_snprintf(s, sizeof s, "存储卡还需 %u MB 空间", nu.need), toast(s);
    else if (state != NU_DONE)
        toast(said[state]);
    else if (nu.label)
        fwupdate_tf(); /* stock's TF card update: its battery check and confirm */
    else
        toast("中文固件已下载，请使用 TF 卡升级");
    return 0;
}

/* A second press cancels: the transfer ends at its next progress call. */
static int online_click(void *ctx, void *event) {
    if (nu.state == NU_RUNNING) {
        nu.cancel = 1;
        show("正在取消…");
    } else if (nu.state != NU_IDLE) {
        /* ended, not yet joined by wait_done */
    } else if (I(g_power_capacity, 0) < NU_BATTERY && !g_usbdet_value) {
        toast("请先充电后再更新"); /* before the download, as stock's updater after it */
    } else if (!need_link(online_click, ctx)) {
        /* held for Smart Wi-Fi's link (a press while it waits takes its place), or none */
    } else {
        nu.cancel = nu.percent = nu.stage = nu.base = 0;
        nu.state = NU_RUNNING;
        if (pthread_create(&nu.thread, 0, worker, 0)) {
            nu.state = NU_IDLE;
            toast("无法开始下载");
        } else {
            show("检查中…");
            timer_add(wait_done, (void *)0, 500);
        }
    }
    return 0;
}

/* Leaving the page cancels: the card may be unmounted or shared over USB next. As 2, unless a press
 * cancelled already: the part, closed by then, stays for the next press to carry on from. */
static int label_gone(void *ctx, void *event) {
    if (nu.label == ctx) nu.label = 0, nu.cancel = nu.cancel ? nu.cancel : 2;
    return 0;
}

extern void pod_rows(void *win, const char *view_name, int first, int n, const char *const *icons);

/* systemset_fwupdate_page_init: stock's two rows (0x4c8660), each button named by its index, which
 * stock's click (0x4c84cc) reads with atoi: 0 TF card update, 1 Shanling's server. Renamed "2", the
 * second does nothing in stock's click and ours runs. */
int ringnav_fwupdate(void *win, void *ctx) {
    static const char *const icons[] = { 0 };
    int result = stock_fwupdate_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_fwupdate", 1) : (void *)0;
    void *button = view && widget_count_children(view) == 2 ? widget_get_child(widget_get_child(view, 1), 0) : 0;
    if (button) {
        widget_set_name(button, "2");
        widget_on(button, EVT_CLICK, online_click, 0);
        nu.label = widget_get_child(button, 0);
        widget_on(nu.label, EVT_DESTROY, label_gone, nu.label);
        if (nu.state == NU_RUNNING)
            show("正在下载…");
        else if (*nc.tag)
            named(); /* else stock's name, as it built the row */
        pod_rows(win, "scroll_view_fwupdate", 26 /* POD_REMIND_DAILY */, 1, icons);
    }
    return result;
}

static void *nc_worker(void *unused) {
    char tag[64] = "";
    long code = 0;
    int ok = !fetch(NU_REPO, 0, tag, sizeof tag, &code, 0, 0) && code == 302 && *tag;
    if (ok) tk_snprintf(nc.tag, sizeof nc.tag, "%s", strcmp(tag, Q2POD_TAG) > 0 ? tag : "");
    nc.ok = ok;
    nc.busy = 0;
    return 0;
}

/* power_poll: a release newer than this build told without a press. Only while Wi-Fi is on anyway
 * (Smart Wi-Fi's radio isn't woken for it), Network Update's header-only request for the latest tag
 * (no API request), a day apart, an hour after one that failed. Once a release, on Home with the
 * screen on; the tag is recorded first, so a toast missed isn't shown again. --dev builds have no
 * tag to compare and check nothing. System Update's Remind Daily Off checks nothing either, and
 * takes back a tag found: its row stops reading "Update to X". */
void netupdate_poll(void) {
    unsigned now = time_now_ms();
    unsigned long tid;
    char s[80];
    if (!*Q2POD_TAG) return;
    if (!ringnav_remind_daily()) { /* not while a check thread owns the tag, as named() */
        if (!nc.busy) *nc.tag = 0, nc.at = 0; /* back On: check at once */
        return;
    }
    if (nc.busy || (nc.polled && now - nc.polled < NU_POLL_MS)) return;
    nc.polled = now | !now;
    void *top = window_manager_get_top_window(window_manager());
    if (*nc.tag && g_backlight_status && top &&
        !tk_strcmp(widget_get_prop_str(top, "name", ""), "home_page")) {
        if (!nc.read) nc.read = 1, BLOB_IO(NU_TOLD, nc.told, 0);
        if (strncmp(nc.told, nc.tag, sizeof nc.tag)) {
            memcpy(nc.told, nc.tag, sizeof nc.told);
            BLOB_IO(NU_TOLD, nc.told, 1);
            tk_snprintf(s, sizeof s, "Q2 Pod %s 已发布，请用网络更新升级", nc.tag);
            toast(s);
            return;
        }
    }
    if ((nc.at && now - nc.at < (nc.ok ? NU_EVERY_MS : NU_AGAIN_MS)) || nu.state == NU_RUNNING ||
        get_wifisignal() <= 0)
        return;
    nc.at = now | !now, nc.busy = 1;
    if (pthread_create(&tid, 0, nc_worker, 0))
        nc.busy = 0;
    else
        pthread_detach(tid);
}

/* The Apps page (System settings): Check for Updates, then a row per app in sources.list, its state
 * as the row's detail. One job at a time on one thread: a check (the list, then the latest tags) on
 * opening or from Check for Updates, an install, a remove or Spotify's erase (Streaming →
 * Configuration's, shelf_erase). Leaving the page leaves it running; its progress shows when the
 * page opens again. */
#define SH_JSON "/tmp/q2-shelf.json"
#define SH_LIST SH_DIR "/sources.list" /* the last list fetched, for the page offline */
/* This fork's App Shelf list, not upstream's: the fork carries its own copy of sources.list,
 * so a merged upstream release can never silently change which card apps the Chinese build
 * offers. Firmware updates come from Q2POD_REPO (tools/build.py REPO) below. */
#define SH_LIST_URL "https://raw.githubusercontent.com/Zoezo01/q2-pod-zh/main/sources.list"
#define SH_STALE_MS (10 * 60 * 1000ull)
#define SH_RECHECK_MS (60 * 1000ull) /* Check for Updates' least gap: an API request per app */
enum { JOB_CHECK = 1, JOB_INSTALL, JOB_REMOVE, JOB_ERASE };
enum { ST_INSTALL, ST_UPDATE, ST_INSTALLED };
enum { ROW_CHECK = SH_MAX }; /* map's row that is no app: Check for Updates, the first */

static struct {
    unsigned long thread;
    volatile int running, cancel, percent, stage, result; /* cancel, percent: progress()'s pair */
    int job, app, armed, rows, n, map[SH_MAX + 1];        /* map: a row's app or ROW_CHECK */
    int manual;                                           /* the check is Check for Updates' */
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

/* The folder a remove is deleting or an install moving into place (stage 3 on), else 0: addon.c
 * opens no page there. */
const char *shelf_moving(void) { return sh.stage >= 3 ? shelf_busy() : 0; }

/* glibc 2.28 MIPS o32 stat v3: st_mode at byte 20; 0 when path is missing. */
static unsigned file_mode(const char *path) {
    unsigned info[36];
    return __xstat(3, path, info) ? 0 : info[5] & 0170000;
}

static char *get(const char *url, long *code) { return get_by(url, code, SH_JSON, &sh.cancel); }

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

/* A check: the list fetched into sh.next, kept as SH_LIST with any app it dropped that is
 * still installed (so it can be removed), else the page's own; then each listed one's latest
 * release. A dropped app's is never asked, nor installed: dropping it is what stops a taken-over
 * repo's releases reaching a Q2 (GHSA-8wpr-pmqx-538x).
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
        if (!listed && file_mode(path))
            sh.next[n] = apps[i], sh.next[n].dropped = 1, *sh.next[n++].latest = 0;
    }
    for (int k = 0; fresh && k < n; ++k)
        m += snprintf(text + m, sizeof text - m, "%s%s %s\n", sh.next[k].dropped ? "-" : "",
                      sh.next[k].repo, sh.next[k].name);
    if (fresh) mkdir(SH_DIR, 0755), blob_io(SH_LIST, SH_LIST ".tmp", text, m, 1);
    sh.n = n;
    if (!n) sh.result = SH_NET, ok = 0; /* no list at all: said, and the next press fetches it */
    for (int k = 0; k < n && !sh.cancel; ++k) {
        if (sh.next[k].dropped) continue;
        int r = release(&sh.next[k], tag, url, &size);
        if (r == SH_MISSING) continue; /* checked: no release yet */
        ok &= !r;
        if (!sh.result) sh.result = r; /* SH_NET or SH_BUSY, for sh_told */
    }
    return ok;
}

/* Whether the list as it is now, fetched for this install, still names app i's repo: SH_OK, SH_NET
 * with no list to be had, SH_MISSING once the repo is dropped or gone from it, SH_CANCELLED when
 * cancelled meanwhile. The page's list may be the card's of days ago (opened with no Wi-Fi, the
 * opening check stopped by a press, or the list out of reach while GitHub's API answered;
 * GHSA-8wpr-pmqx-538x).
 * ponytail: an install needs raw.githubusercontent.com as well as the API; a network that blocks
 * only the list's host installs nothing. */
static int listed(int i) {
    shelf_app now[SH_MAX];
    long code = 0;
    char *list = get(SH_LIST_URL, &code);
    int n = list ? shelf_sources(list, now, SH_MAX) : 0, r = n ? SH_MISSING : SH_NET;
    free(list);
    for (int k = 0; k < n; ++k)
        if (!now[k].dropped && !strcmp(now[k].repo, apps[i].repo)) r = SH_OK;
    return sh.cancel ? SH_CANCELLED : r;
}

/* Cancel lands during the download, else after the step it was pressed in; never once moving
 * starts. ponytail: TLS and curl's length check are the only checks on the zip; unzip's CRCs the
 * only ones in it. */
static int install(int i) {
    char tag[32], url[256], part[96], path[96];
    unsigned size = 0, total = 0;
    long code = 0;
    int r = apps[i].dropped ? SH_MISSING : release(&apps[i], tag, url, &size);
    if (sh.cancel) return SH_CANCELLED;
    if (r) return r;
    system("rm -rf " STAGING); /* an interrupted install's, too */
    mkdir(SH_DIR, 0755), mkdir(STAGING, 0755), mkdir(SH_DIR "/apps", 0755);
    if ((r = room(size, &sh.need))) return r;
    staged(part, sizeof part, i, ".part");
    void *f = fopen(part, "wb");
    if (!f) return SH_CARD;
    sh.stage = 1;
    int err = fetch(url, f, 0, 0, &code, &sh.cancel, 0), bad = ferror(f);
    bad = fclose(f) || bad;
    r = sh.cancel ? SH_CANCELLED : bad ? SH_CARD : err ? SH_NET : code != 200 ? SH_MISSING : SH_OK;
    if (!r) sh.stage = 2, r = shelf_list(i, &total);
    if (!r) r = room(total, &sh.need);
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
    } else if (sh.job == JOB_INSTALL) {
        int r = listed(i);
        sh.result = r ? r : install(i);
    } else {
        /* Erase Spotify Data: the add-on's logs go too, as spot.log names the account and each
         * track played */
        if (sh.job == JOB_ERASE)
            system("rm -rf " SPOT_DIR "/cache " SPOT_DIR "/.cache-size " SPOT_DIR
                   "/spot.log " SPOT_DIR "/aplay.log " SPOT_DIR "/load.log"),
                rmdir(SPOT_DIR);
        else {
            addon_quit(apps[i].dir); /* USB storage may take the card meanwhile */
            /* ponytail: read once; USB storage taking the card during the unlinks still says removed */
            if (card_mounted()) {
                if (is(i, "rockbox") && t >= 0) shelf_remove(t); /* no themes without Rockbox */
                shelf_remove(i);
            } else
                sh.result = SH_CARD; /* the bare mount point: nothing removed */
        }
        system("sync");
    }
    sh.running = 0;
    return 0;
}

/* With a tag file ST_INSTALLED, or ST_UPDATE when the latest differs; -1 without one. */
static int sh_tagged(int i) {
    char path[96], tag[40];
    app_file(path, sizeof path, i, ".tag");
    state_read(path, tag, sizeof tag); /* "\n" and the tag */
    if (!tag[1]) return -1;
    return *apps[i].latest && strcmp(tag + 1, apps[i].latest) ? ST_UPDATE : ST_INSTALLED;
}

/* sh_tagged's state, else ST_UPDATE for a manual install. */
static int sh_state(int i) {
    char path[96];
    int s = sh_tagged(i);
    if (s >= 0) return s;
    if (*apps[i].bin) {
        snprintf(path, sizeof path, CARD "/%s", apps[i].bin);
        if (file_mode(path) == 0100000) return ST_UPDATE;
    }
    return ST_INSTALL;
}

static void sh_text(int r) {
    static const char *const stages[] = { "检查中…", "下载中", "解包中…", "安装中…",
                                          "移除中…" };
    static const char *const states[] = { "安装", "更新", "已安装" };
    int i = sh.map[r], armed = sh.armed == r + 1,
        busy = (sh.job == JOB_INSTALL || sh.job == JOB_REMOVE) && sh.app == i;
    char s[40];
    const char *t = s;
    if (i == ROW_CHECK && sh.job == JOB_CHECK)
        t = "检查中…";
    else if (i == ROW_CHECK) /* with no list yet, the press loads it */
        t = !shelf_n ? "请先连接 Wi-Fi 以加载列表" : theme()->sep[0].n ? " " : "";
    else if (busy && sh.cancel)
        t = "正在取消…";
    else if (busy && sh.stage == 1 && sh.percent)
        snprintf(s, sizeof s, "正在下载 %d%%", sh.percent);
    else if (busy)
        t = stages[sh.stage];
    else if (armed && sh_state(i) == ST_INSTALLED)
        t = "再按一次以移除";
    else if (armed) /* docs/boot.md#rockbox */
        t = "开机自动启动，可在“默认启动”中更改。再按一次以安装";
    else
        t = states[sh_state(i)];
    widget_set_text_utf8(widget_get_child(sh.row[r], 1), t);
}

static void sh_rows(void);
static void sh_refresh(void) {
    for (int r = 0; sh.page && r < sh.rows; ++r) sh_text(r);
}

/* A check's toast: how many rows' apps have a newer release than their tag file's (a manual
 * install's Update isn't counted: its version is unknown); with none, from Check for Updates
 * (manual), why it failed or that none have. */
static void sh_told(int manual, const char *failed) {
    char s[40];
    int n = 0;
    for (int r = 1; r < sh.rows; ++r) n += sh_tagged(sh.map[r]) == ST_UPDATE; /* past ROW_CHECK */
    if (n)
        snprintf(s, sizeof s, "有 %d 个应用可更新", n), toast(s);
    else if (manual && failed)
        toast(failed);
    else if (manual)
        toast("所有应用都是最新版本");
}

static int sh_wait(const void *unused) {
    static const char *const said[] = {
        [SH_NET] = "无法连接 GitHub",           [SH_CARD] = "无法写入存储卡",
        [SH_MISSING] = "此版本暂无更新", [SH_BAD] = "下载内容不是可用的应用",
        [SH_CANCELLED] = "已取消下载",     [SH_BUSY] = "GitHub 繁忙，请稍后再试",
    };
    char s[64];
    if (sh.running) {
        if (sh.job != JOB_CHECK) reset_poweroptions_timer(1, 1, 0); /* standby, auto power-off */
        sh_refresh();
        return 8; /* RET_REPEAT */
    }
    pthread_join(sh.thread, 0);
    int job = sh.job, r = sh.result, i = sh.app, manual = sh.manual;
    sh.job = 0;
    /* its files replaced or gone: an update that failed or was cancelled before it stopped its app leaves it running, still tracked,
     * and a remove cut off by USB storage leaves it marked to start again with the card */
    if (r == SH_OK && (job == JOB_REMOVE || job == JOB_INSTALL)) addon_forget(apps[i].dir);
    if (job == JOB_CHECK) /* the rows built again: the list may have changed */
        memcpy(apps, sh.next, sizeof apps), shelf_n = sh.n, sh.rows = -1;
    if (job == JOB_ERASE || (job != JOB_CHECK && is(i, "spotify")))
        spot_stop(); /* spotify.c forgets the old one */
    sh_rows();
    if (job == JOB_CHECK && !sh.cancel && sh.page &&
        window_manager_get_top_window(window_manager()) == sh.page) /* not left or covered */
        sh_told(manual, r ? said[r] : 0);
    else if (job == JOB_INSTALL && r == SH_SPACE)
        snprintf(s, sizeof s, "还需 %u MB 空间", sh.need), toast(s);
    else if ((job == JOB_INSTALL || job == JOB_REMOVE) && r)
        toast(said[r]);
    else if (job == JOB_INSTALL && is(i, "rockbox"))
        toast("Rockbox 已就绪，可在主菜单找到");
    else if (job == JOB_INSTALL && is(i, "themes"))
        toast("Rockbox 主题已就绪，可在 Rockbox 的主题设置中选择");
    else if (job == JOB_INSTALL)
        snprintf(s, sizeof s, "%s 已就绪，可在%s中找到", apps[i].name,
                 addon_is_game(apps[i].id) ? "游戏" : "流媒体"),
            toast(s);
    else if (job == JOB_REMOVE)
        snprintf(s, sizeof s, "%s 已移除", apps[i].name), toast(s);
    else if (job == JOB_ERASE)
        stream_erased(), toast("Spotify 登录信息与缓存已清除"); /* rows first: toast blocks */
    return 0;
}

static void sh_start(int job, int i) {
    if (job == JOB_ERASE || (is(i, "spotify") && job != JOB_CHECK))
        spot_stop(); /* on this thread: spotify.c's state too */
    sh.job = job, sh.app = i, sh.cancel = sh.percent = sh.result = sh.manual = 0, sh.running = 1;
    sh.stage = job == JOB_REMOVE ? 4 : 0;
    if (pthread_create(&sh.thread, 0, sh_worker, 0)) {
        sh.job = sh.running = 0;
        toast("无法开始下载");
    } else
        timer_add(sh_wait, (void *)0, 500);
}

/* Install and Update at once; Installed's Remove and Rockbox's first Install (it becomes the
 * power-on default) on a second press of the same row. Pressed again, the running install cancels.
 * Check for Updates checks now (loading the list, with none yet), unless the last whole check is
 * under SH_RECHECK_MS old: then it says that check's count again. */
static int sh_click(void *ctx, void *event) {
    int r = (int)(long)ctx, i = sh.map[r], armed = sh.armed == r + 1;
    sh.armed = 0;
    if (i == ROW_CHECK && sh.job == JOB_CHECK) {
        sh.manual = 1; /* checking already, the opening check maybe: its end answers the press */
    } else if (sh.job == JOB_CHECK) { /* needs no answer: stopped, so the next press acts */
        sh.cancel = 1;
        toast("已停止检查更新");
    } else if (sh.job) {
        if (sh.job == JOB_INSTALL && sh.app == i && sh.stage < 3)
            sh.cancel = 1; /* not once moving */
        else if (sh.job != JOB_INSTALL || sh.app != i)
            toast("请等待当前任务完成");
    } else if (i != ROW_CHECK && sh_state(i) == ST_INSTALLED) {
        if (!armed)
            sh.armed = r + 1;
        else
            sh_start(JOB_REMOVE, i);
    } else if (!need_link(sh_click, ctx)) {
        /* held for Smart Wi-Fi's link (a press while it waits takes its place), or none */
    } else if (i == ROW_CHECK && sh.checked && now_ms() - sh.checked < SH_RECHECK_MS)
        sh_told(1, 0);
    else if (i == ROW_CHECK)
        sh_start(JOB_CHECK, 0), sh.manual = 1;
    else if (is(i, "themes") && !rockbox_available())
        toast("请先安装 Rockbox");
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

/* The rows: Check for Updates, then the list's apps in its order, Rockbox Themes only with Rockbox
 * on the card and a dropped app only while installed; built again when a job ends changes which. */
static void sh_rows(void) {
    int map[SH_MAX + 1] = { ROW_CHECK }, n = 1;
    for (int i = 0; i < shelf_n; ++i)
        if ((!is(i, "themes") || rockbox_available()) && (!apps[i].dropped || sh_tagged(i) >= 0))
            map[n++] = i;
    if (sh.page && (n != sh.rows || memcmp(map, sh.map, sizeof map))) {
        memcpy(sh.map, map, sizeof map);
        sh.rows = 0;
        void *view = page_list(sh.page, sh.page, 0, "应用商店", n, 64);
        for (int r = 0; r < n; ++r)
            /* sh_text writes the detail: a blank one, or a theme would centre the name over it */
            sh.row[r] = page_row_detail(
                view, r, map[r] == ROW_CHECK ? "检查更新" : apps[map[r]].name,
                theme()->sep[0].n ? " " : "", sh_click);
        sh.rows = n;
    }
    sh_refresh();
}

/* Streaming → Configuration's Erase Spotify Data (navigation.c): with start, Spotify's login, cache
 * and logs erased on this thread, unless a job is running (a check stops, as an App Store row's
 * press stops it, so the next press acts); whether an erase is running. */
int shelf_erase(int start) {
    if (start && sh.job == JOB_CHECK)
        sh.cancel = 1, toast("已停止检查更新");
    else if (start && sh.job)
        toast("请等待当前任务完成");
    else if (start)
        sh_start(JOB_ERASE, -1);
    return sh.job == JOB_ERASE;
}

/* The opening check, held for Smart Wi-Fi's link (smart_hold). */
static int sh_check(void *ctx, void *event) {
    if (!sh.job && get_wifisignal() > 0) sh_start(JOB_CHECK, 0), sh_refresh();
    return 0;
}

/* System settings' Apps row. The card's last list, until a check fetches the list again: with Wi-Fi
 * when the last whole check is over SH_STALE_MS old (or none), as GitHub allows 60 unauthenticated
 * API requests an hour; while Smart Wi-Fi brings the radio back, once its link is up. */
int shelf_open(void *ctx, void *event) {
    if (sh.page || !(sh.page = page_open("apps_page", sh_closed, page_keyup))) return 0;
    if (sh.job != JOB_CHECK && !shelf_n) { /* an erase can run from Configuration before it opens */
        unsigned n = 0;
        char *list = read_all(SH_LIST, 1u << 16, &n);
        shelf_n = shelf_sources(list, apps, SH_MAX);
        free(list);
    }
    sh_rows();
    if (!sh.job && (!sh.checked || now_ms() - sh.checked > SH_STALE_MS)) {
        if (get_wifisignal() > 0)
            sh_start(JOB_CHECK, 0), sh_refresh();
        else
            /* once Smart Wi-Fi's link is up; a row pressed while it waits takes its place and acts on
             * the list shown (an install fetches the list first), and the next open checks */
            smart_hold(sh_check, 0, "apps_page");
    }
    return 0;
}

/* Smart Wi-Fi (smart_wifi.c smart_needed): Network Update's download or an Apps job keeps the radio on. */
int netupdate_busy(void) { return nu.state == NU_RUNNING || sh.job; }
#endif
