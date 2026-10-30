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

extern volatile unsigned library_gen;
#define CARD PEQ_ROOT "/mnt/mmc"
#define CARD_URL "/mnt/mmc/"
#define SCOPE_MAX 131072u
#define PATH_MAX_SCOPE 1024

typedef struct scope_rule {
    struct scope_rule *next;
    unsigned length;
    int visible;
    char path[];
} scope_rule;
static struct {
    scope_rule *rules;
    char *sql;
    unsigned generation, bytes;
    int ready, failed;
} scope __attribute__((section(".scratch")));

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

static void scope_walk(char *path, unsigned depth) {
    unsigned n = strlen(path);
    if (depth > 64 || n + 258 >= PATH_MAX_SCOPE) { scope.failed = 1; return; }
    scope_snprintf(path + n, PATH_MAX_SCOPE - n, "/database.ignore");
    int ignore = mode_is(path, 0100000);
    scope_snprintf(path + n, PATH_MAX_SCOPE - n, "/database.unignore");
    int unignore = mode_is(path, 0100000);
    path[n] = 0;
    if (ignore || unignore) scope_add(path + sizeof(PEQ_ROOT) - 1, unignore);
    void *dir = opendir(path);
    if (!dir) return;
    for (struct dirent *e; !scope.failed && (e = readdir(dir));) {
        /* dot folders: ., .., and caches such as .sldp and .coverflow, thousands of files */
        if (e->d_name[0] == '.' ||
            (e->d_type && e->d_type != 4)) continue;
        int count = scope_snprintf(path + n, PATH_MAX_SCOPE - n, "/%s", e->d_name);
        if (count < 0 || (unsigned)count >= PATH_MAX_SCOPE - n) { scope.failed = 1; break; }
        if (e->d_type == 4 || mode_is(path, 0040000)) scope_walk(path, depth + 1);
        path[n] = 0;
    }
    closedir(dir); path[n] = 0;
}

static void scope_read(void) {
    if (scope.ready && scope.generation == library_gen) return;
    while (scope.rules) {
        scope_rule *next = scope.rules->next;
        free(scope.rules); scope.rules = next;
    }
    free(scope.sql); scope.sql = 0; scope.bytes = 32; scope.failed = 0;
    unsigned generation = library_gen;
    char path[PATH_MAX_SCOPE] = CARD;
    scope_walk(path, 0);
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
    scope.generation = generation; scope.ready = 1;
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
