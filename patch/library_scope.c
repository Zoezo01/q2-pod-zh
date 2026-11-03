/* Rockbox-style directory markers affect browsing, never indexing or playlist storage. */
#include "peq.h"
#include "library_scope.h"
#ifdef PEQ_HOST
#include <dirent.h>
#include <pthread.h>
#include <sys/stat.h>
#define scope_snprintf snprintf
static pthread_mutex_t scope_lock = PTHREAD_MUTEX_INITIALIZER;
#else
#include "stock.h"
#define scope_snprintf tk_snprintf
static unsigned scope_lock[6] __attribute__((section(".scratch")));
#endif

#define CARD PEQ_ROOT "/mnt/mmc"
#define CARD_URL "/mnt/mmc/"
#define SCOPE_MAX 131072u
#define PATH_MAX_SCOPE 1024
/* a line per marker folder: 0 or 1, then its path */
#define RULES PEQ_ROOT "/mnt/data/q2-library-scope"

typedef struct scope_rule {
    struct scope_rule *next;
    unsigned length;
    int visible;
    char path[];
} scope_rule;
static struct {
    scope_rule *rules;
    char *sql;
    unsigned bytes;
    int ready, failed;
} scope __attribute__((section(".scratch")));
static struct {
    void *out;
    unsigned bytes;
    int failed;
} walk __attribute__((section(".scratch"))); /* the scan thread's */

static int mode_is(const char *path, unsigned mode) {
#ifdef PEQ_HOST
    struct stat s;
    return !lstat(path, &s) && (s.st_mode & 0170000) == mode;
#else
    unsigned s[36];
    /* Card formats do not expose symlinks; dirent symlinks are never followed below. */
    return !__xstat(3, path, s) && (s[5] & 0170000) == mode;
#endif
}

static void scope_add(const char *path, int visible) {
    unsigned n = strlen(path);
    if (scope.bytes + n * 2 + 96 > SCOPE_MAX) { scope.failed = 1; return; }
    scope_rule *r = calloc(1, sizeof(*r) + n + 2);
    if (!r) { scope.failed = 1; return; }
    memcpy(r->path, path, n);
    r->path[n] = '/'; r->length = n + 1; r->visible = visible;
    /* Descendants precede ancestors: CASE's first match gives the closest marker. */
    scope_rule **at = &scope.rules;
    while (*at && (*at)->length > r->length) at = &(*at)->next;
    r->next = *at; *at = r;
    scope.bytes += n * 2 + 96;
}

/* A marker file in the folder, as named or hidden with a leading dot (#154). */
static int marker(char *path, unsigned n, const char *name) {
    scope_snprintf(path + n, PATH_MAX_SCOPE - n, "/%s", name);
    int found = mode_is(path, 0100000);
    scope_snprintf(path + n, PATH_MAX_SCOPE - n, "/.%s", name);
    found |= mode_is(path, 0100000);
    path[n] = 0;
    return found;
}

static void scope_walk(char *path, unsigned depth) {
    unsigned n = strlen(path);
    if (depth > 64 || n + 258 >= PATH_MAX_SCOPE) { walk.failed = 1; return; }
    int ignore = marker(path, n, "database.ignore"), unignore = marker(path, n, "database.unignore");
    if (ignore || unignore) {
        const char *card = path + sizeof(PEQ_ROOT) - 1;
        unsigned length = strlen(card);
        walk.bytes += length * 2 + 96; /* as scope_add counts it */
        if (walk.bytes > SCOPE_MAX || fwrite(unignore ? "1" : "0", 1, 1, walk.out) != 1 ||
            fwrite(card, 1, length, walk.out) != length || fwrite("\n", 1, 1, walk.out) != 1)
            walk.failed = 1;
    }
    void *dir = opendir(path);
    if (!dir) return;
    for (struct dirent *e; !walk.failed && (e = readdir(dir));) {
        /* dot folders: ., .., and caches such as .sldp and .coverflow, thousands of files */
        if (e->d_name[0] == '.' ||
            (e->d_type && e->d_type != 4)) continue;
        int count = scope_snprintf(path + n, PATH_MAX_SCOPE - n, "/%s", e->d_name);
        if (count < 0 || (unsigned)count >= PATH_MAX_SCOPE - n) { walk.failed = 1; break; }
        if (e->d_type == 4 || mode_is(path, 0040000)) scope_walk(path, depth + 1);
        path[n] = 0;
    }
    closedir(dir); path[n] = 0;
}

/* A library update walks the card on stock's scan thread and saves the markers to RULES, so a
 * query never waits on the card; a failed walk leaves no file, so no markers.
 * ponytail: a folder scan walks the whole card too; walk only that folder if that gets slow. */
void library_scope_scan(void) {
    char path[PATH_MAX_SCOPE] = CARD;
    walk.bytes = 32; walk.failed = 0;
    walk.out = fopen(RULES ".tmp", "wb");
    if (walk.out) {
        scope_walk(path, 0);
        if (fflush(walk.out) || fsync(fileno(walk.out))) walk.failed = 1;
        if (fclose(walk.out)) walk.failed = 1;
    }
    if (!walk.out || walk.failed || rename(RULES ".tmp", RULES)) {
        unlink(RULES ".tmp");
        unlink(RULES);
    }
    pthread_mutex_lock(&scope_lock);
    scope.ready = 0;
    pthread_mutex_unlock(&scope_lock);
}

static void scope_read(void) {
    if (scope.ready) return;
    while (scope.rules) {
        scope_rule *next = scope.rules->next;
        free(scope.rules); scope.rules = next;
    }
    free(scope.sql); scope.sql = 0; scope.bytes = 32; scope.failed = 0;
    void *f = fopen(RULES, "rb");
    char *text = f ? calloc(1, SCOPE_MAX) : 0;
    unsigned size = text ? fread(text, 1, SCOPE_MAX, f) : 0, start = 0;
    if (f) fclose(f);
    if (f && (!text || size == SCOPE_MAX)) scope.failed = 1;
    for (unsigned i = 0; !scope.failed && i < size; ++i) {
        if (text[i] != '\n') continue;
        char *line = text + start;
        text[i] = 0; start = i + 1;
        if ((line[0] != '0' && line[0] != '1') || line[1] != '/') scope.failed = 1;
        else scope_add(line + 1, line[0] == '1');
    }
    if (start != size) scope.failed = 1; /* a cut line */
    free(text);
    if (!scope.failed) scope.sql = calloc(1, scope.bytes);
    if (scope.sql) {
        char *p = scope.sql;
        memcpy(p, "case ", 5); p += 5;
        for (scope_rule *r = scope.rules; r; r = r->next) {
            unsigned characters = 0;
            for (const unsigned char *s = (const unsigned char *)r->path; *s; ++s)
                if ((*s & 0xc0) != 0x80) ++characters;
            p += scope_snprintf(p, scope.bytes - (unsigned)(p - scope.sql),
                                "when substr(fileurl,1,%u)='", characters);
            for (const char *s = r->path; *s; ++s) {
                *p++ = *s;
                if (*s == '\'') *p++ = '\'';
            }
            p += scope_snprintf(p, scope.bytes - (unsigned)(p - scope.sql),
                                "' collate nocase then %d ", r->visible);
        }
        if (scope.rules) memcpy(p, "else 1 end", 11);
        else memcpy(scope.sql, "1", 2);
    }
    scope.ready = 1;
}

char *library_scope_query(const char *sql) {
    const char *token = sql ? strstr(sql, LIBRARY_SCOPE_TOKEN) : 0;
    if (!token) return sql ? strdup(sql) : 0;
    pthread_mutex_lock(&scope_lock);
    scope_read();
    /* ponytail: a card too deep or with too many markers to walk, or no memory, ignores the
     * markers: the whole library shows rather than none of it. */
    const char *predicate = scope.sql ? scope.sql : "1";
    unsigned prefix = (unsigned)(token - sql), n = strlen(predicate);
    const char *tail = token + sizeof(LIBRARY_SCOPE_TOKEN) - 1;
    char *expanded = calloc(1, prefix + n + strlen(tail) + 1);
    if (expanded) {
        memcpy(expanded, sql, prefix);
        memcpy(expanded + prefix, predicate, n);
        memcpy(expanded + prefix + n, tail, strlen(tail) + 1);
    }
    pthread_mutex_unlock(&scope_lock);
    return expanded;
}

int library_scope_hidden(const char *path) {
    if (!path || strncasecmp(path, CARD_URL, sizeof(CARD_URL) - 1)) return 0;
    pthread_mutex_lock(&scope_lock);
    scope_read();
    int hidden = 0;
    if (scope.sql)
        for (scope_rule *r = scope.rules; r; r = r->next)
            if (!strncasecmp(path, r->path, r->length)) { hidden = !r->visible; break; }
    pthread_mutex_unlock(&scope_lock);
    return hidden;
}
