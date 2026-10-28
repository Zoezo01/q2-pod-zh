#include "peq.h"
#include "chapters.h"
#ifdef PEQ_HOST
#include <signal.h>
#include <sys/wait.h>
#include <errno.h>
#define CH_NONBLOCK O_NONBLOCK
#define CH_CLOEXEC O_CLOEXEC
#else
/* demo has no imports for these three libc calls; use Linux o32, as spot_shut does. */
int chapter_pipe2(int *p, int flags), chapter_kill(int pid, int sig);
int chapter_execv(const char *path, char *const *args);
__asm__(".set push\n.set noreorder\n"
        ".globl chapter_pipe2\nchapter_pipe2:\nli $v0,4328\nsyscall\n"
        "beqz $a3,1f\nnop\nli $v0,-1\n1: jr $ra\nnop\n"
        ".globl chapter_kill\nchapter_kill:\nli $v0,4037\nsyscall\njr $ra\nnop\n"
        ".globl chapter_execv\nchapter_execv:\nmove $a2,$zero\nli $v0,4011\nsyscall\njr $ra\nnop\n"
        ".set pop\n");
#define pipe2 chapter_pipe2
#define kill chapter_kill
#define execv chapter_execv
#define CH_NONBLOCK 0x80
#define CH_CLOEXEC 0x80000
#endif

static int decimal(char **p, unsigned long long *v) {
    *v = 0;
    char *s = *p;
    while (**p >= '0' && **p <= '9') {
        unsigned d = (unsigned)(*(*p)++ - '0');
        if (*v > (0x7fffffffffffffffULL - d) / 10) return 0;
        *v = *v * 10 + d;
    }
    return *p != s;
}

/* Decode ffmetadata escapes while splitting logical lines, including escaped newlines. */
int chapters_parse(char *raw, chapter *rows) {
    int n = 0, active = 0, have = 0;
    unsigned long long num = 0, den = 0, start = 0;
    if (strncmp(raw, ";FFMETADATA1", 12) || (raw[12] != '\n' && raw[12] != '\r')) return -1;
    char title[256] = "";
    for (char *p = raw; ; ) {
        char *line = p, *out = p;
        while (*p && *p != '\n') {
            if (*p == '\\' && p[1]) ++p;
            *out++ = *p++;
        }
        int end = !*p;
        if (!end) ++p;
        if (out > line && out[-1] == '\r') --out;
        *out = 0;
        if ((end && !*line) || line[0] == '[') {
            if (active) {
                if (have != 3 || !num || !den || num > 0x7fffffff || den > 0x7fffffff ||
                    start / den > 0x7fffffff / num) return -1;
                unsigned long long sec = start / den * num + start % den * num / den;
                if (sec > 0x7fffffff || n == CHAPTER_MAX || (n && sec < (unsigned)rows[n-1].sec)) return -1;
                rows[n].sec = (int)sec;
                memcpy(rows[n].title, title, strlen(title) + 1);
                ++n;
            }
            if (end && !*line) return n;
            active = !strcmp(line, "[CHAPTER]");
            have = 0;
            num = den = start = 0;
            title[0] = 0;
        } else if (active && !strncmp(line, "TIMEBASE=", 9)) {
            char *s = line + 9;
            if (!decimal(&s, &num) || *s++ != '/' || !decimal(&s, &den) || *s) return -1;
            have |= 1;
        } else if (active && !strncmp(line, "START=", 6)) {
            char *s = line + 6;
            if (!decimal(&s, &start) || *s) return -1;
            have |= 2;
        } else if (active && !strncmp(line, "title=", 6)) {
            unsigned len = strlen(line + 6);
            if (len >= sizeof title) len = sizeof title - 1;
            /* Do not cut a UTF-8 character in half. */
            while (len && ((unsigned char)line[6 + len] & 0xc0) == 0x80) --len;
            memcpy(title, line + 6, len);
            title[len] = 0;
        }
    }
}

void chapters_cancel(chapters *c) {
    c->failed = 1;
    if (c->fd >= 0) close(c->fd);
    c->fd = -1;
    if (c->pid > 0) kill(c->pid, 9);
}

chapters *chapters_start(const char *path, unsigned long long now) {
    chapters *c = calloc(1, sizeof *c);
    if (!c) return 0;
    c->fd = -1;
    c->at = now;
    int p[2];
    if (pipe2(p, CH_CLOEXEC)) { c->failed = c->done = 1; return c; }
    c->pid = fork();
    if (!c->pid) {
        char *const args[] = { "/usr/bin/ffmpeg", "-nostdin", "-v", "error", "-i", (char *)path,
                              "-f", "ffmetadata", "pipe:1", 0 };
        if (p[1] == 1) fcntl(1, 2, 0); /* F_SETFD: clear CLOEXEC */
        else if (dup2(p[1], 1) < 0) child_exit(127);
        int err = open("/dev/null", 1);
        if (err >= 0) dup2(err, 2);
        execv(args[0], args);
        child_exit(127);
    }
    close(p[1]);
    c->fd = p[0];
    if (c->pid < 0 || fcntl(c->fd, 4, CH_NONBLOCK)) {
        chapters_cancel(c);
        if (c->pid < 0) c->done = 1;
    }
    return c;
}

int chapters_poll(chapters *c, unsigned long long now) {
    if (c->done) return 1;
    if (now - c->at >= 10000 && !c->failed) chapters_cancel(c);
    /* Bound each UI tick too, even if the producer keeps the pipe full. */
    for (int i = 0; c->fd >= 0 && i < 16; ++i) {
        int room = CHAPTER_BYTES - c->used;
        if (!room) { chapters_cancel(c); break; }
        int n = read(c->fd, c->raw + c->used, (unsigned)(room > 4096 ? 4096 : room));
        if (n > 0) c->used += n;
        else if (!n) { close(c->fd); c->fd = -1; }
        else {
            int e = *__errno_location();
            if (e != 4 && e != 11) chapters_cancel(c);
            break;
        }
    }
    int status = 0, result = c->pid > 0 ? waitpid(c->pid, &status, 1) : 1; /* WNOHANG */
    if (result < 0 && *__errno_location() == 4) return 0;
    if (!result) return 0;
    if (result < 0 || status) c->failed = 1;
    c->pid = 0;
    /* Reap only after draining output, which may exceed one tick. */
    if (c->fd >= 0 && !c->failed) return 0;
    if (c->fd >= 0) close(c->fd);
    c->fd = -1;
    c->raw[c->used] = 0;
    c->count = c->failed ? -1 : chapters_parse(c->raw, c->rows);
    c->failed = c->count < 0;
    return c->done = 1;
}

static unsigned long long be(const unsigned char *p, int n) {
    unsigned long long v = 0;
    while (n--) v = v << 8 | *p++;
    return v;
}

/* Samples in the stsz/stz2 tables under [at, end): moov, trak, mdia, minf and stbl are walked. */
static unsigned long long mp4_samples(void *f, unsigned long long at, unsigned long long end, int depth) {
    unsigned long long n = 0;
    while (end - at >= 8) {
        unsigned char h[16];
        /* fseek's long: a file stock's fopen opens (no O_LARGEFILE) is under 2 GB anyway */
        if (at > 0x7fffffff || fseek(f, (long)at, 0) || fread(h, 1, 8, f) != 8) break;
        unsigned long long size = be(h, 4), head = 8;
        if (size == 1) {
            if (fread(h + 8, 1, 8, f) != 8) break;
            size = be(h + 8, 8);
            head = 16;
        } else if (!size)
            size = end - at;
        if (size < head || size > end - at) break;
        if (depth < 5 && (!memcmp(h + 4, "moov", 4) || !memcmp(h + 4, "trak", 4) ||
                          !memcmp(h + 4, "mdia", 4) || !memcmp(h + 4, "minf", 4) ||
                          !memcmp(h + 4, "stbl", 4)))
            n += mp4_samples(f, at + head, at + size, depth + 1);
        else if ((!memcmp(h + 4, "stsz", 4) || !memcmp(h + 4, "stz2", 4)) && size >= head + 12 &&
                 fread(h, 1, 12, f) == 12)
            n += be(h + 8, 4); /* version/flags, sample size (stz2: field size), count */
        at += size;
    }
    return n;
}

unsigned long long mp4_index_bytes(const char *path) {
    const char *ext = path ? strrchr(path, '.') : 0;
    if (!ext || (strcasecmp(ext, ".m4b") && strcasecmp(ext, ".m4a") && strcasecmp(ext, ".mp4")))
        return 0;
    void *f = fopen(path, "rb");
    if (!f) return *__errno_location() == MP4_EOVERFLOW ? ~0ULL : 0;
    unsigned long long n = mp4_samples(f, 0, ~0ULL, 0);
    fclose(f);
    return n * MP4_SAMPLE_BYTES;
}

int mp4_fits(const char *path) {
    unsigned long long need = mp4_index_bytes(path);
    if (!need) return 1;
    char buf[512];
    void *f = fopen("/proc/meminfo", "rb");
    unsigned n = f ? fread(buf, 1, sizeof buf - 1, f) : 0;
    if (f) fclose(f);
    buf[n] = 0;
    const char *p = strstr(buf, "MemAvailable:");
    if (!p) return 1; /* no estimate: play as stock does */
    return need / 1024 + MP4_SPARE_KB <= (unsigned long long)atoi(p + 13);
}
