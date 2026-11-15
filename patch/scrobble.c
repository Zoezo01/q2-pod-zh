/* Scrobble upload (docs/internals.md#scrobbling): the card's .scrobbler.log goes to ListenBrainz
 * and Last.fm, with the accounts in /mnt/mmc/.scrobble.ini, on one pthread through demo's libcurl.
 * A batch leaves the log only once every configured service took it. */
#include "peq.h"
#ifndef PEQ_HOST
#include "stock.h"
#endif

#define LOG_FILE PEQ_ROOT "/mnt/mmc/.scrobbler.log"
#define INI_FILE PEQ_ROOT "/mnt/mmc/.scrobble.ini"
#define CA_FILE PEQ_ROOT "/etc/scrobble-ca.pem" /* supplied by the firmware, no card setup */
#define BATCH 50                                  /* Last.fm's limit; ListenBrainz takes 100 */
/* Rockbox's lastfm_scrobbler on this card: tags from its 2168-byte id3 buffer, a title from the
 * file name, the numbers */
#define LINE_BYTES 2560
#define BODY_MAX (384 << 10) /* 50 lines of LINE_BYTES, URL-encoded, and the names */
#define LOG_HEAD (1 << 20) /* a window of the log: its whole lines; the rest waits for the next */
#define LB_URL "https://api.listenbrainz.org/1/submit-listens"
#define FM_URL "https://ws.audioscrobbler.com/2.0/"

enum { LB_TOKEN, FM_USER, FM_PASS, FM_KEY, FM_SECRET, FM_SK, CONFIG_N };
typedef struct {
    char *p;
    unsigned n, cap; /* n == cap: overflowed */
} buf_t;
typedef struct {
    char s[1024];
    unsigned n;
} reply_t;
typedef struct {
    char *artist, *album, *title, *length, *time;
} entry_t;
typedef struct {
    char name[16];
    const char *value;
} param_t;

static struct {
    unsigned lock[16]; /* a zeroed pthread mutex, 24 bytes on MIPS glibc */
    unsigned long thread;
    volatile int running, done, ok, sent;
    char cfg[CONFIG_N][256];
} up __attribute__((section(".scratch")));

static const char header[] = "#AUDIOSCROBBLER/1.1\n#TZ/UTC\n#CLIENT/Q2 Pod\n";

/* Bit 0 ListenBrainz, bit 1 Last.fm: its API pair with a session key (SESSION_KEY, which
 * tools/lastfm_key.py puts in the password's place) or with the user and password that fetch one
 * at each upload. toolsReadConfig leaves the value alone for a missing file. */
static int read_config(char c[][256]) {
    static const char *const keys[] = { "TOKEN",   "USER",       "PASSWORD",
                                        "API_KEY", "API_SECRET", "SESSION_KEY" };
    for (int i = 0; i < CONFIG_N; i++) {
        c[i][0] = 0;
        toolsReadConfig(INI_FILE, i ? "LASTFM" : "LISTENBRAINZ", keys[i], c[i], "");
    }
    return (c[LB_TOKEN][0] != 0) |
           (c[FM_KEY][0] && c[FM_SECRET][0] && (c[FM_SK][0] || (c[FM_USER][0] && c[FM_PASS][0])))
               << 1;
}

int scrobble_ready(void) {
    char c[CONFIG_N][256];
    return read_config(c);
}

/* [SCROBBLE] key's number, 0 when missing. PERCENT: the share of a song heard that logs a listen
 * (navigation.c's resume_poll). */
int scrobble_value(const char *key) {
    char v[256] = "";
    toolsReadConfig(INI_FILE, "SCROBBLE", key, v, "");
    return atoi(v);
}

/* [SCROBBLE] key=1, off unless 1. ALBUM_ARTIST: listens log the album artist in place of the
 * artist, for tags whose artist carries "feat." guests. AUTO: uploads start by themselves
 * (scrobble_ui.c's scrobble_auto). */
int scrobble_flag(const char *key) { return scrobble_value(key) == 1; }

/* The UI thread's listen; the upload's rewrite holds the same lock. 0 when the card didn't take
 * it whole: the log is cut back to what it was (or removed, if this was its start), so no torn
 * line joins the next listen. Cut after the close, which may still write the buffer's rest. */
int scrobble_append(const char *line, unsigned n) {
    pthread_mutex_lock(up.lock);
    int fresh = access(LOG_FILE, 0) != 0;
    void *f = fopen(LOG_FILE, "ab");
    long at = f && !fseek(f, 0, 2) ? ftell(f) : -1;
    int ok = at >= 0 && (!fresh || fwrite(header, sizeof header - 1, 1, f) == 1) &&
             fwrite(line, n, 1, f) == 1;
    if (f && fclose(f)) ok = 0;
    if (!ok && fresh)
        unlink(LOG_FILE); /* a headerless log would stay so */
    else if (!ok && at >= 0 && (f = fopen(LOG_FILE, "r+b"))) {
        /* ponytail: a failed cut leaves the torn line, which the next listen joins */
        if (ftruncate(fileno(f), at)) {}
        fclose(f);
    }
    pthread_mutex_unlock(up.lock);
    return ok;
}

/* path's bytes in a calloc'd buffer of *size + 1 (NUL-terminated), or 0 and *size 0 when it can't
 * be opened or read or holds over max bytes. Shared with navigation.c. */
void *read_all(const char *path, unsigned max, unsigned *size) {
    void *f = fopen(path, "rb");
    char *p = 0;
    long n = f && !fseek(f, 0, 2) ? ftell(f) : -1;
    if (n >= 0 && (unsigned long)n <= max && !fseek(f, 0, 0) && (p = calloc(1, (unsigned)n + 1)) &&
        fread(p, 1, (unsigned)n, f) != (unsigned)n) {
        free(p);
        p = 0;
    }
    if (f) fclose(f);
    *size = p ? (unsigned)n : 0;
    return p;
}

/* path's first bytes, at most max, as read_all's (a file longer than max is no failure). */
static char *read_head(const char *path, unsigned max, unsigned *size) {
    void *f = fopen(path, "rb");
    long n = f && !fseek(f, 0, 2) ? ftell(f) : -1;
    unsigned want = (unsigned long)n > max ? max : (unsigned)n;
    char *p = n >= 0 && !fseek(f, 0, 0) ? calloc(1, want + 1) : 0;
    *size = p ? (unsigned)fread(p, 1, want, f) : 0;
    if (f) fclose(f);
    return p;
}

/* mode 0 raw, 1 URL-encoded, 2 a JSON string's contents (control bytes become spaces). */
static void put(buf_t *b, const char *s, int mode) {
    static const char hex[] = "0123456789ABCDEF";
    for (; s && *s; s++) {
        unsigned char c = (unsigned char)*s;
        char e[3] = { (char)c };
        unsigned k = 1;
        if (mode == 1 && !((c | 32) >= 'a' && (c | 32) <= 'z') && !(c >= '0' && c <= '9') &&
            c != '-' && c != '_' && c != '.' && c != '~')
            e[0] = '%', e[1] = hex[c >> 4], e[2] = hex[c & 15], k = 3;
        else if (mode == 2 && (c == '"' || c == '\\'))
            e[0] = '\\', e[1] = (char)c, k = 2;
        else if (mode == 2 && c < 32)
            e[0] = ' ';
        if (b->n + k >= b->cap) {
            b->n = b->cap;
            return;
        }
        for (unsigned i = 0; i < k; i++) b->p[b->n++] = e[i];
        b->p[b->n] = 0;
    }
}

/* A line's time: 1 to 10 digits (a 32-bit time_t's), not all zero. The field goes raw into the
 * ListenBrainz JSON and Last.fm's form, so a damaged one (a byte of junk after the digits, which
 * atoi alone passed) would spoil the whole batch; such a line is skipped with the other junk. */
static int epoch(const char *s) {
    int nonzero = 0, n = 0;
    for (; *s; ++s, ++n) {
        if (*s < '0' || *s > '9') return 0;
        nonzero |= *s != '0';
    }
    return nonzero && n <= 10;
}

/* One Rockbox line: artist, album, title, track, length, rating, time, MusicBrainz ID. Only
 * listened (L) lines with an artist, a title and a time of digits (epoch) are scrobbled. */
static int parse(char *line, entry_t *e) {
    char *f[8] = { line };
    int n = 1;
    if (strlen(line) > LINE_BYTES) return 0; /* would overrun BODY_MAX, and block every upload */
    for (char *s = line; *s && n < 8; s++)
        if (*s == '\t') *s = 0, f[n++] = s + 1;
    e->artist = f[0], e->album = f[1], e->title = f[2], e->length = f[4], e->time = f[6];
    return n >= 7 && *f[0] && *f[2] && f[5][0] == 'L' && epoch(f[6]);
}

static unsigned got(const char *data, unsigned size, unsigned n, void *ctx) {
    reply_t *r = ctx;
    for (unsigned i = 0; i < size * n && r->n < sizeof r->s - 1; i++) r->s[r->n++] = data[i];
    r->s[r->n] = 0;
    return size * n;
}

/* POST body; the HTTP status, 0 when no reply. Credentials are sent only over verified TLS;
 * the firmware's CA bundle supplies roots missing from the stock image. */
static long post(const char *url, const char *body, const char *auth, reply_t *r) {
    void *c = curl_easy_init(), *h = 0;
    long code = 0;
    if (!c) return 0;
    if (auth) h = curl_slist_append(curl_slist_append(0, auth), "Content-Type: application/json");
    r->n = 0;
    r->s[0] = 0;
    curl_easy_setopt(c, 10002, url);                 /* CURLOPT_URL */
    curl_easy_setopt(c, 10015, body);                /* POSTFIELDS */
    if (h) curl_easy_setopt(c, 10023, h);            /* HTTPHEADER */
    curl_easy_setopt(c, 20011, got);                 /* WRITEFUNCTION */
    curl_easy_setopt(c, 10001, r);                   /* WRITEDATA */
    curl_easy_setopt(c, 99, 1L);                     /* NOSIGNAL: off the UI thread */
    curl_easy_setopt(c, 78, 15L);                    /* CONNECTTIMEOUT */
    curl_easy_setopt(c, 13, 60L);                    /* TIMEOUT */
    curl_easy_setopt(c, 10065, CA_FILE);              /* CAINFO */
    curl_easy_setopt(c, 64, 1L);                 /* SSL_VERIFYPEER */
    curl_easy_setopt(c, 81, 2L);             /* SSL_VERIFYHOST */
    curl_easy_setopt(c, 20108, tls_untimed); /* SSL_CTX_FUNCTION: no date checks */
    if (!curl_easy_perform(c)) curl_easy_getinfo(c, 0x200002, &code); /* RESPONSE_CODE */
    curl_easy_cleanup(c);
    curl_slist_free_all(h);
    return code;
}

static int listenbrainz(const entry_t *e, int n, buf_t *b, reply_t *r) {
    char auth[300];
    b->n = 0;
    put(b, "{\"listen_type\":\"import\",\"payload\":[", 0);
    for (int i = 0; i < n; i++) {
        put(b, i ? ",{\"listened_at\":" : "{\"listened_at\":", 0);
        put(b, e[i].time, 0);
        put(b, ",\"track_metadata\":{\"artist_name\":\"", 0);
        put(b, e[i].artist, 2);
        put(b, "\",\"track_name\":\"", 0);
        put(b, e[i].title, 2);
        if (*e[i].album) {
            put(b, "\",\"release_name\":\"", 0);
            put(b, e[i].album, 2);
        }
        put(b, "\",\"additional_info\":{\"submission_client\":\"Q2 Pod\"}}}", 0);
    }
    put(b, "]}", 0);
    tk_snprintf(auth, sizeof auth, "Authorization: Token %s", up.cfg[LB_TOKEN]);
    return b->n < b->cap && post(LB_URL, b->p, auth, r) == 200;
}

static void add(param_t *p, int *n, const char *name, int i, const char *value) {
    if (!value || !*value) return;
    tk_snprintf(p[*n].name, sizeof p->name, i < 0 ? "%s" : "%s[%d]", name, i);
    p[(*n)++].value = value;
}

static int by_name(const void *a, const void *b) {
    return strcmp(((const param_t *)a)->name, ((const param_t *)b)->name);
}

/* A signed Last.fm call: api_sig is the MD5 of the sorted names and values, then the secret. */
static int lastfm(param_t *p, int n, buf_t *b, reply_t *r) {
    unsigned ctx[32];
    unsigned char d[16];
    char sig[33];
    add(p, &n, "api_key", -1, up.cfg[FM_KEY]);
    qsort(p, (unsigned)n, sizeof *p, by_name);
    MD5_Init(ctx);
    b->n = 0;
    for (int i = 0; i < n; i++) {
        MD5_Update(ctx, p[i].name, strlen(p[i].name));
        MD5_Update(ctx, p[i].value, strlen(p[i].value));
        put(b, p[i].name, 1);
        put(b, "=", 0);
        put(b, p[i].value, 1);
        put(b, "&", 0);
    }
    MD5_Update(ctx, up.cfg[FM_SECRET], strlen(up.cfg[FM_SECRET]));
    MD5_Final(d, ctx);
    for (int i = 0; i < 16; i++) tk_snprintf(sig + 2 * i, 3, "%02x", d[i]);
    put(b, "api_sig=", 0);
    put(b, sig, 0);
    put(b, "&format=json", 0);
    /* A refusal is {"message":...,"error":9}. Only the key counts: track.scrobble's reply echoes
     * each listen's names, and a name is JSON-escaped (a quote is \"), so none reads as "error":.
     * ponytail: rests on Last.fm's compact JSON (no space before the colon); parse the reply if
     * that changes. */
    return b->n < b->cap && post(FM_URL, b->p, 0, r) == 200 && !strstr(r->s, "\"error\":");
}

/* auth.getMobileSession: the session key for the batches, once per upload, when the file has
 * none: the one call that carries the password. */
static int session(char *sk, buf_t *b, reply_t *r) {
    param_t p[5];
    int n = 0;
    add(p, &n, "method", -1, "auth.getMobileSession");
    add(p, &n, "username", -1, up.cfg[FM_USER]);
    add(p, &n, "password", -1, up.cfg[FM_PASS]);
    const char *k = lastfm(p, n, b, r) ? strstr(r->s, "\"key\":\"") : 0;
    unsigned i = 0;
    for (; k && k[7 + i] && k[7 + i] != '"' && i < 63; i++) sk[i] = k[7 + i];
    sk[i] = 0;
    return i > 0;
}

static int scrobble_lastfm(const entry_t *e, int n, const char *sk, buf_t *b, reply_t *r) {
    param_t p[4 + 5 * BATCH];
    int m = 0;
    add(p, &m, "method", -1, "track.scrobble");
    add(p, &m, "sk", -1, sk);
    for (int i = 0; i < n; i++) {
        add(p, &m, "artist", i, e[i].artist);
        add(p, &m, "track", i, e[i].title);
        add(p, &m, "album", i, e[i].album);
        add(p, &m, "timestamp", i, e[i].time);
        if (atoi(e[i].length) > 0) add(p, &m, "duration", i, e[i].length);
    }
    return lastfm(p, m, b, r);
}

/* Moves the first done bytes' scrobbles to .sent and keeps the rest under the header. The log is
 * only appended to meanwhile, so those bytes are the ones uploaded. The new log is written first, so
 * a full card fails before .sent gains anything; .sent is synced before the rename drops them. Only
 * the done bytes are held: the rest is copied through. */
static int retire(unsigned done) {
    pthread_mutex_lock(up.lock);
    char *p = calloc(1, done + 1), chunk[4096];
    void *in = p ? fopen(LOG_FILE, "rb") : 0;
    void *f = in && fread(p, 1, done, in) == done ? fopen(LOG_FILE ".tmp", "wb") : 0;
    int ok = f != 0;
    if (f) {
        ok = fwrite(header, sizeof header - 1, 1, f) == 1;
        for (unsigned k; ok && (k = (unsigned)fread(chunk, 1, sizeof chunk, in));)
            ok = fwrite(chunk, k, 1, f) == 1;
        if (ferror(in) || fflush(f) || fsync(fileno(f))) ok = 0;
        if (fclose(f)) ok = 0;
    }
    if (in) fclose(in);
    void *sent = ok ? fopen(LOG_FILE ".sent", "ab") : 0;
    ok = sent != 0;
    for (unsigned i = 0, j; ok && i < done; i = j) {
        for (j = i; j < done && p[j++] != '\n';) {}
        if (p[i] != '#') ok = fwrite(p + i, j - i, 1, sent) == 1;
    }
    if (sent) {
        if (fflush(sent) || fsync(fileno(sent))) ok = 0;
        if (fclose(sent)) ok = 0;
    }
    if (ok) ok = !rename(LOG_FILE ".tmp", LOG_FILE);
    if (!ok) unlink(LOG_FILE ".tmp"); /* a full card gets its room back */
    free(p);
    pthread_mutex_unlock(up.lock);
    return ok;
}

static void *worker(void *unused) {
    reply_t r;
    entry_t e[BATCH];
    char got[64] = "";
    const char *sk = up.cfg[FM_SK][0] ? up.cfg[FM_SK] : got; /* the file's key, else session()'s */
    unsigned size;
    int lb = !!up.cfg[LB_TOKEN][0], fm = !!up.cfg[FM_KEY][0], sent = 0;
    buf_t b = { calloc(1, BODY_MAX), 0, BODY_MAX };
    int ok = b.p != 0;
    /* A window at a time, each retired before the next is read, until one comes back short. A full
     * window always retires something (whole lines, or the junk), so the passes end. ponytail: each
     * pass rewrites the rest of the log under the lock, so card writes grow with the square of the
     * backlog (a 3 MB log writes about 3 MB extra, 10 MB about 45 MB); carrying an offset across
     * windows and rewriting once would remove it. */
    for (int more = 1; ok && more;) {
        unsigned done = 0;
        pthread_mutex_lock(up.lock);
        char *log = read_head(LOG_FILE, LOG_HEAD, &size);
        pthread_mutex_unlock(up.lock);
        for (unsigned at = 0; ok && log;) {
            int n = 0;
            while (n < BATCH && at < size) { /* complete lines only */
                unsigned j = at;
                while (j < size && log[j] != '\n') j++;
                if (j == size) break;
                log[j] = 0;
                if (log[at] != '#' && parse(log + at, &e[n])) n++;
                at = j + 1;
            }
            /* a cut line waits; in a full window, one longer than any real line is junk */
            if (!n) {
                done = size == LOG_HEAD && size - at > LINE_BYTES ? size : at;
                break;
            }
            ok = (!fm || *sk || session(got, &b, &r)) && (!lb || listenbrainz(e, n, &b, &r)) &&
                 (!fm || scrobble_lastfm(e, n, sk, &b, &r));
            if (ok) done = at, sent += n;
        }
        free(log);
        more = size == LOG_HEAD && done;
        if (done && !retire(done)) ok = 0;
    }
    free(b.p);
    up.sent = sent;
    up.ok = ok;
    up.done = 1;
    return 0;
}

/* 1 started, 0 already running, -1 no thread. */
int scrobble_start(void) {
    if (up.running) return 0;
    int on = read_config(up.cfg);
    if (!on) return -1; /* nothing would take the batches, so none may leave the log */
    if (!(on & 2)) up.cfg[FM_KEY][0] = 0; /* an incomplete Last.fm is off */
    up.done = 0;
    if (pthread_create(&up.thread, 0, worker, 0)) return -1;
    up.running = 1;
    return 1;
}

/* Smart Wi-Fi (smart_wifi.c smart_needed): an upload in progress, the user's or the automatic one,
 * keeps the radio on. */
int scrobble_busy(void) { return up.running; }

/* 0 while running or idle; when it ends, 1 or -1 (a batch failed) once, with the scrobbles sent. */
int scrobble_poll(int *sent) {
    if (!up.running || !up.done) return 0;
    pthread_join(up.thread, 0);
    up.running = 0;
    *sent = up.sent;
    return up.ok ? 1 : -1;
}
