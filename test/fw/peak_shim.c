/* The firmware's libc allocations, counted: linked into the host fw.so with ld --wrap by peak.py, so only the
 * firmware's own calloc/realloc/free/strdup calls come here (Python's and libc's own do not).
 * Bytes are the bytes asked for, by pointer in a side table: a pointer this never handed out
 * (stock's, the test's) is freed uncounted. Single-threaded, as the host build is. */
#include <stdlib.h>
#include <string.h>

void *__real_calloc(size_t, size_t);
void *__real_realloc(void *, size_t);
void __real_free(void *);
char *__real_strdup(const char *);

long q2m_live, q2m_peak, q2m_calls;

/* Also the Python deque model's: a row a deque holds (+n at a push, -n when it goes). */
void q2m_add(long n) {
    q2m_live += n;
    q2m_calls += n > 0;
    if (q2m_live > q2m_peak) q2m_peak = q2m_live;
}

#define SLOTS (1u << 22) /* ponytail: fixed table, aborts past half full; grow it if a path allocates 2M blocks */
#define DEAD ((void *)1)
static struct { void *p; size_t n; } tab[SLOTS];
static unsigned used;

static unsigned hash(void *p) { return (unsigned)(((size_t)p >> 4) * 2654435761u); }
static void note(void *p, size_t n) {
    if (!p) return;
    if (++used > SLOTS / 2) abort();
    unsigned h = hash(p);
    while (tab[h & (SLOTS - 1)].p && tab[h & (SLOTS - 1)].p != DEAD) ++h;
    tab[h & (SLOTS - 1)].p = p, tab[h & (SLOTS - 1)].n = n;
    q2m_add((long)n);
}
static void drop(void *p) {
    for (unsigned h = hash(p); p && tab[h & (SLOTS - 1)].p; ++h)
        if (tab[h & (SLOTS - 1)].p == p) {
            q2m_live -= (long)tab[h & (SLOTS - 1)].n;
            tab[h & (SLOTS - 1)].p = DEAD;
            return;
        }
}

void *__wrap_calloc(size_t a, size_t b) {
    void *p = __real_calloc(a, b);
    note(p, a * b);
    return p;
}
void *__wrap_realloc(void *old, size_t n) {
    void *p = __real_realloc(old, n);
    if (p) drop(old), note(p, n);
    return p;
}
void __wrap_free(void *p) {
    drop(p);
    __real_free(p);
}
char *__wrap_strdup(const char *s) {
    char *p = __real_strdup(s);
    note(p, strlen(s) + 1);
    return p;
}
