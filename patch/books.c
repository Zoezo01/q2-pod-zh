/* Books (docs/internals.md#books): the card's Books folder, .txt and .epub, read a page at a time.
 * An EPUB's spine is turned into plain text once, on a pthread, into BOOK_DIR, and then read like a
 * .txt: a window of the file around the page, laid out with the stock default font, the wheel
 * turning pages. The page last read is kept per book in BOOK_MARKS. */
#include "offsets.inc"
#include "peq.h"
#ifndef PEQ_HOST
#include "stock.h"
#endif

#define BOOK_DIR PEQ_ROOT "/mnt/mmc/.books"
#define BOOK_MARKS "/mnt/data/ringnav-books"
#define CD_MAX (2 << 20)   /* a zip's central directory, at most */
#define ITEM_MAX (8 << 20) /* one zip entry, packed or not, at most */
#define BOOK_LINE 128      /* characters on one line, at most */
#define BOOK_BACK 3072     /* bytes laid out again to find the previous page */

static unsigned le(const unsigned char *p, int n) {
    unsigned v = 0;
    while (n--) v = v << 8 | p[n];
    return v;
}

static int space(int c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r'; }

/* A hex digit's value; 16 or more for any other character. */
static unsigned digit(int c) {
    return c >= '0' && c <= '9' ? c - '0' + 0u : (c | 0x20) - 'a' + 10u;
}

/* Raw deflate (RFC 1951), after zlib's puff: in[0..n) into out[0..cap). */
typedef struct {
    const unsigned char *in;
    unsigned n, pos, bits, cnt, len, cap, err;
    unsigned char *out;
} inf_t;
typedef struct {
    short count[16], symbol[288];
} huff_t;

static unsigned bits(inf_t *s, unsigned need) {
    unsigned v = s->bits; /* need is at most 13, so v holds at most 20 bits */
    while (s->cnt < need) {
        if (s->pos >= s->n) return s->err = 1, 0;
        v |= (unsigned)s->in[s->pos++] << s->cnt;
        s->cnt += 8;
    }
    s->bits = v >> need;
    s->cnt -= need;
    return v & ((1u << need) - 1);
}

/* Canonical codes from lengths; -1 when over-subscribed (an incomplete set decodes until a gap). */
static int build(huff_t *h, const unsigned char *length, int n) {
    short offs[16];
    memset(h->count, 0, sizeof h->count);
    for (int i = 0; i < n; ++i) h->count[length[i]]++;
    int left = 1;
    for (int len = 1; len < 16; ++len)
        if ((left = 2 * left - h->count[len]) < 0) return -1;
    offs[1] = 0;
    for (int len = 1; len < 15; ++len) offs[len + 1] = offs[len] + h->count[len];
    for (int i = 0; i < n; ++i)
        if (length[i]) h->symbol[offs[length[i]]++] = i;
    return 0;
}

static int decode(inf_t *s, const huff_t *h) {
    int code = 0, first = 0, index = 0;
    for (int len = 1; len < 16; ++len) {
        code |= bits(s, 1);
        int count = h->count[len];
        if (code - count < first) return h->symbol[index + code - first];
        index += count;
        first = (first + count) << 1;
        code <<= 1;
    }
    return -1;
}

/* A length (len) or distance symbol's extra bits and base. */
static int extra(int i, int len) {
    return len ? (i < 8 ? 0 : (i - 4) >> 2) : (i < 4 ? 0 : (i - 2) >> 1);
}
static int base(int i, int len) {
    int b = len ? 3 : 1;
    for (int k = 0; k < i; ++k) b += 1 << extra(k, len);
    return len && i == 28 ? 258 : b;
}

static int codes(inf_t *s, const huff_t *lit, const huff_t *dist) {
    for (;;) {
        int sym = decode(s, lit);
        if (s->err || sym < 0) return -1;
        if (sym == 256) return 0;
        if (sym < 256) {
            if (s->len >= s->cap) return -1;
            s->out[s->len++] = (unsigned char)sym;
            continue;
        }
        if ((sym -= 257) >= 29) return -1;
        unsigned len = base(sym, 1) + bits(s, sym == 28 ? 0 : extra(sym, 1));
        int d = decode(s, dist);
        if (d < 0 || d >= 30) return -1;
        unsigned back = base(d, 0) + bits(s, extra(d, 0));
        if (s->err || back > s->len || len > s->cap - s->len) return -1;
        for (; len; --len, ++s->len) s->out[s->len] = s->out[s->len - back];
    }
}

/* The inflated length, or -1 for a malformed stream or one longer than cap. */
int book_inflate(const unsigned char *in, unsigned n, unsigned char *out, unsigned cap) {
    static const unsigned char order[19] = { 16, 17, 18, 0, 8,  7, 9,  6, 10, 5,
                                             11, 4,  12, 3, 13, 2, 14, 1, 15 };
    inf_t s = { in, n, 0, 0, 0, 0, cap, 0, out };
    huff_t lit, dist;
    unsigned char length[320];
    for (int last = 0; !last;) {
        last = bits(&s, 1);
        int type = bits(&s, 2), nlen = 288, ndist = 30;
        const unsigned char *dl = length + 288; /* the distance codes' lengths */
        if (s.err || type == 3) return -1;
        if (!type) { /* stored */
            s.bits = s.cnt = 0;
            if (s.pos + 4 > n) return -1;
            unsigned len = le(in + s.pos, 2);
            if ((le(in + s.pos + 2, 2) ^ 0xffff) != len || len > n - s.pos - 4 || len > cap - s.len)
                return -1;
            memcpy(out + s.len, in + s.pos + 4, len);
            s.pos += 4 + len, s.len += len;
            continue;
        }
        if (type == 1) { /* fixed codes */
            for (int i = 0; i < 288; ++i) length[i] = i < 144 ? 8 : i < 256 ? 9 : i < 280 ? 7 : 8;
            for (int i = 0; i < 30; ++i) length[288 + i] = 5;
        } else {
            nlen = bits(&s, 5) + 257, ndist = bits(&s, 5) + 1;
            int ncode = bits(&s, 4) + 4;
            if (nlen > 286 || ndist > 30) return -1;
            memset(length, 0, 19);
            for (int i = 0; i < ncode; ++i) length[order[i]] = bits(&s, 3);
            if (build(&lit, length, 19)) return -1;
            for (int i = 0; i < nlen + ndist;) {
                int sym = decode(&s, &lit), len = 0, repeat;
                if (s.err || sym < 0) return -1;
                if (sym < 16) {
                    length[i++] = sym;
                    continue;
                }
                if (sym == 16) {
                    if (!i) return -1;
                    len = length[i - 1], repeat = 3 + bits(&s, 2);
                } else
                    repeat = sym == 17 ? 3 + bits(&s, 3) : 11 + bits(&s, 7);
                if (i + repeat > nlen + ndist) return -1;
                while (repeat--) length[i++] = len;
            }
            dl = length + nlen;
        }
        if (build(&lit, length, nlen) || build(&dist, dl, ndist) || codes(&s, &lit, &dist))
            return -1;
    }
    return (int)s.len;
}

/* The zip's central directory, kept while the book is converted. */
typedef struct {
    void *f;
    unsigned char *cd;
    unsigned size;
} zip_t;

static int zip_open(zip_t *z, const char *path) {
    unsigned char *tail = calloc(65558, 1);
    long size = 0, at;
    int ok = 0;
    if (tail && (z->f = fopen(path, "rb")) && !fseek(z->f, 0, 2) && (size = ftell(z->f)) >= 22) {
        at = size > 65557 ? size - 65557 : 0;
        unsigned n = fseek(z->f, at, 0) ? 0 : fread(tail, 1, (unsigned)(size - at), z->f);
        int i = (int)n - 22;
        while (i >= 0 && le(tail + i, 4) != 0x06054b50) --i; /* the end record, last of all */
        unsigned cd = i >= 0 ? le(tail + i + 16, 4) : 0;
        z->size = i >= 0 ? le(tail + i + 12, 4) : 0;
        ok = i >= 0 && z->size <= CD_MAX && cd + z->size <= (unsigned long)size &&
             (z->cd = calloc(z->size + 1, 1)) && !fseek(z->f, cd, 0) &&
             fread(z->cd, 1, z->size, z->f) == z->size;
    }
    free(tail);
    return ok;
}

/* name's entry, NUL-terminated in a new buffer (length *n), or 0. */
static char *zip_read(zip_t *z, const char *name, unsigned *n) {
    unsigned len = strlen(name);
    for (unsigned char *p = z->cd, *end = z->cd + z->size;
         p + 46 <= end && le(p, 4) == 0x02014b50;) {
        unsigned nl = le(p + 28, 2), skip = 46 + nl + le(p + 30, 2) + le(p + 32, 2);
        if (p + 46 + nl > end) break;
        if (nl != len || memcmp(p + 46, name, len)) {
            p += skip;
            continue;
        }
        unsigned method = le(p + 10, 2), packed = le(p + 20, 4), size = le(p + 24, 4);
        unsigned char head[30], *in = 0, *out = 0;
        if ((le(p + 8, 2) & 1) || (method != 8 && (method || packed != size)) ||
            packed > ITEM_MAX || size > ITEM_MAX || fseek(z->f, le(p + 42, 4), 0) ||
            fread(head, 1, 30, z->f) != 30 || le(head, 4) != 0x04034b50 ||
            fseek(z->f, le(head + 26, 2) + le(head + 28, 2), 1) || !(in = calloc(packed + 1, 1)) ||
            fread(in, 1, packed, z->f) != packed) {
            free(in);
            return 0;
        }
        if (!method)
            out = in, in = 0;
        else if ((out = calloc(size + 1, 1)) && book_inflate(in, packed, out, size) != (int)size)
            free(out), out = 0;
        free(in);
        *n = size;
        return (char *)out;
    }
    return 0;
}

/* The next element named name (namespace prefix aside) at or after p: its '<', with *end at its
 * '>'; 0 when there is none. */
static const char *tag(const char *p, const char *name, const char **end) {
    unsigned n = strlen(name);
    for (; *p; ++p) {
        if (*p != '<') continue;
        const char *q = p + 1, *local = q;
        while (*q && !space(*q) && *q != '>' && *q != '/')
            if (*q++ == ':') local = q;
        if ((unsigned)(q - local) != n || memcmp(local, name, n)) continue;
        for (*end = q; **end != '>'; ++*end)
            if (!**end) return 0;
        return p;
    }
    return 0;
}

/* Attribute name's value in the tag [p, end), its length in *len; 0 when it has none. */
static const char *attr(const char *p, const char *end, const char *name, unsigned *len) {
    unsigned n = strlen(name);
    for (; p + n + 2 < end; ++p)
        if (space(*p) && !memcmp(p + 1, name, n) && p[n + 1] == '=' &&
            (p[n + 2] == '"' || p[n + 2] == '\'')) {
            const char *v = p + n + 3, *q = v;
            while (q < end && *q != p[n + 2]) ++q;
            *len = (unsigned)(q - v);
            return q < end ? v : 0;
        }
    return 0;
}

static char *put_utf8(char *o, unsigned c) {
    int k = c < 0x80 ? 0 : c < 0x800 ? 1 : c < 0x10000 ? 2 : 3;
    *o++ = (char)(k ? (0xf0 << (3 - k) & 0xff) | c >> 6 * k : c);
    while (k--) *o++ = (char)(0x80 | (c >> 6 * k & 63));
    return o;
}

/* XHTML s as plain text, in place (it never grows): tags dropped, block elements and <br> as line
 * breaks with at most one blank line, head, style and script skipped, entities as UTF-8, white
 * space runs as one space. Returns the length. */
unsigned book_xhtml(char *s) {
    static const char blocks[] = " p div br li tr hr dt dd h1 h2 h3 h4 h5 h6 blockquote section ";
    static const char named[] = "amp;&lt;<gt;>quot;\"apos;'nbsp; ";
    char *o = s;
    const char *p = s;
    int nl = 2, gap = 0; /* line breaks just written (2: none wanted yet), a space pending */
    while (*p) {
        if (*p == '<') {
            const char *e = p[1] == '!' && p[2] == '-' && p[3] == '-' ? strstr(p, "-->") : p,
                       *name = p + 1 + (p[1] == '/'), *q = name;
            while (*q && !space(*q) && *q != '>' && *q != '/') ++q;
            unsigned n = (unsigned)(q - name);
            char want[24] = " ";
            if (e != p) {
                p = e ? e + 3 : p + strlen(p);
                continue;
            }
            while (*e && *e != '>') ++e;
            if (n < 20) memcpy(want + 1, name, n), want[n + 1] = ' ', want[n + 2] = 0;
            if (n < 20 && p[1] != '/' && e[-1] != '/' && strstr(" head style script ", want)) {
                want[0] = '<', want[1] = '/', memcpy(want + 2, name, n), want[n + 2] = 0;
                if (!(e = strstr(e, want))) break;
                while (*e && *e != '>') ++e;
            } else if (n < 20 && strstr(blocks, want)) {
                gap = 0;
                if (nl < 2) *o++ = '\n', ++nl;
            }
            p = *e ? e + 1 : e;
            continue;
        }
        if (space(*p)) {
            gap = 1, ++p;
            continue;
        }
        unsigned c = 0;
        const char *q = p + 1;
        if (*p == '&') {
            while (*q && *q != ';' && q - p < 12) ++q;
            if (*q == ';' && p[1] == '#') {
                int hex = (p[2] | 0x20) == 'x';
                for (const char *d = p + 2 + hex; d < q && c < 0x110000; ++d) {
                    unsigned v = digit(*d);
                    c = v < (hex ? 16u : 10u) ? c * (hex ? 16 : 10) + v : 0x110000;
                }
            } else if (*q == ';')
                for (const char *t = named, *u; *t; t = u + 2) {
                    for (u = t; *u != ';'; ++u) {}
                    if (u - t == q - p - 1 && !memcmp(t, p + 1, u - t)) c = (unsigned char)u[1];
                }
            if (c - 1 >= 0x10ffff) c = 0;
        }
        if (gap && !nl) *o++ = ' ';
        gap = nl = 0;
        if (c)
            o = put_utf8(o, c), p = q + 1;
        else
            *o++ = *p++;
    }
    while (o > s && o[-1] == '\n') --o;
    *o = 0;
    return (unsigned)(o - s);
}

/* The EPUB at src as plain text into out: the spine's documents in order, each on new pages ('\f').
 * 1 when done; 0 for a book that is not a readable EPUB (DRM included), or when cancel is set. */
int book_convert(const char *src, const char *out, volatile int *cancel) {
    zip_t z = { 0, 0, 0 };
    unsigned n, len, done = 0;
    char *xml = 0, *opf = 0, path[512];
    const char *v, *e, *p, *q, *qe, *id;
    void *f = 0;
    int ok = 0;
    if (!zip_open(&z, src)) goto end;
    if ((xml = zip_read(&z, "META-INF/encryption.xml", &n)) && strstr(xml, "htm"))
        goto end; /* DRM; fonts alone may be obfuscated */
    free(xml);
    if (!(xml = zip_read(&z, "META-INF/container.xml", &n)) || !(p = tag(xml, "rootfile", &e)) ||
        !(v = attr(p, e, "full-path", &len)) || len >= sizeof path / 2)
        goto end;
    memcpy(path, v, len), path[len] = 0;
    unsigned dir = len;
    while (dir && path[dir - 1] != '/') --dir;
    if (!(opf = zip_read(&z, path, &n)) || !(f = fopen(out, "wb"))) goto end;
    /* ponytail: each itemref scans the manifest; an id index if big books prepare slowly */
    for (p = opf; !*cancel && (p = tag(p, "itemref", &e)); p = e) {
        if (!(v = attr(p, e, "idref", &len))) continue;
        for (q = opf; (q = tag(q, "item", &qe)); q = qe)
            if ((id = attr(q, qe, "id", &n)) && n == len && !memcmp(id, v, len)) break;
        if (!q || ((id = attr(q, qe, "media-type", &n)) && n > 6 && !memcmp(id, "image/", 6)) ||
            !(q = attr(q, qe, "href", &len)))
            continue;      /* not in the manifest, or a picture (a cover) */
        unsigned at = dir; /* the OPF's folder, then the href, %XX decoded, up to any #fragment */
        for (const char *h = q, *end = q + len; h < end && *h != '#' && at < sizeof path - 1; ++h) {
            int seg = !at || path[at - 1] == '/'; /* at a segment's start */
            if (seg && end - h >= 2 && !memcmp(h, "./", 2)) {
                ++h; /* a ./ segment */
                continue;
            }
            if (seg && end - h >= 3 && !memcmp(h, "../", 3)) {
                if (at) --at; /* a ../ segment drops the folder before it */
                while (at && path[at - 1] != '/') --at;
                h += 2;
                continue;
            }
            unsigned hi = h + 2 < end ? digit(h[1]) : 16, lo = hi < 16 ? digit(h[2]) : 16;
            unsigned x = lo < 16 ? hi * 16 + lo : 256;
            path[at++] = *h == '%' && x < 256 ? (h += 2, (char)x) : *h;
        }
        path[at] = 0;
        char *doc = zip_read(&z, path, &n);
        if (!doc) goto end; /* missing or unreadable: an encrypted book */
        n = book_xhtml(doc);
        int failed = n && ((done && fwrite("\f\n", 1, 2, f) != 2) || fwrite(doc, 1, n, f) != n);
        free(doc);
        if (failed) goto end;
        done += n;
    }
    ok = done && !*cancel;
end:
    if (f && fclose(f)) ok = 0;
    if (z.f) fclose(z.f);
    free(z.cd);
    free(xml);
    free(opf);
    return ok;
}

/* The character at t[*i] (of n), *i stepped past it: UTF-8, or else the one byte as Latin-1. */
unsigned book_char(const unsigned char *t, unsigned n, unsigned *i) {
    unsigned c = t[*i], k = c >= 0xf8 ? 0 : c >= 0xf0 ? 3 : c >= 0xe0 ? 2 : c >= 0xc0 ? 1 : 0;
    unsigned v = c & (0x3f >> k);
    for (unsigned j = 1; j <= k; ++j) {
        if (*i + j >= n || (t[*i + j] & 0xc0) != 0x80) {
            k = 0;
            break;
        }
        v = v << 6 | (t[*i + j] & 63);
    }
    *i += 1 + k;
    return k ? v : c;
}

/* One page of t[0..n) from pos: at most rows lines of width, measure giving a character's
 * width, each line passed to line when given. Lines wrap at the last space, else mid-word; a
 * '\f' starts a new page; line breaks at the top of a page are dropped. Returns where the next
 * page starts. */
unsigned book_page(const unsigned char *t, unsigned n, unsigned pos, int rows, int width,
                   int (*measure)(void *, unsigned),
                   void (*line)(void *, int, const unsigned *, int), void *ctx) {
    unsigned wc[BOOK_LINE];
    for (int row = 0, stop = 0; row < rows && pos < n && !stop; ++row) {
        unsigned i = pos, cut = pos, brk = 0;
        int k = 0, x = 0, kb = 0;
        for (;;) {
            if (i >= n) {
                cut = i;
                break;
            }
            unsigned at = i, c = book_char(t, n, &i);
            if (c == '\r' || c == 0xfeff || ((c == '\n' || c == '\f') && !k && !row)) continue;
            if (c == '\n' || c == '\f') {
                cut = c == '\n' ? i : at;
                stop = c == '\f';
                break;
            }
            if (c == '\t' || c == 0xa0) c = ' ';
            int w = measure(ctx, c);
            if (k && (k == BOOK_LINE || x + w > width)) {
                if (c == ' ')
                    cut = i;
                else if (brk)
                    k = kb, cut = brk;
                else
                    cut = at;
                break;
            }
            if (c == ' ') kb = k, brk = i;
            wc[k++] = c;
            x += w;
        }
        if (line) line(ctx, row, wc, k);
        pos = cut;
    }
    return pos;
}

/* The start of the page before the one at pos: pages laid out from a line start up to BOOK_BACK
 * bytes back, until one reaches pos. */
unsigned book_back(const unsigned char *t, unsigned n, unsigned pos, int rows, int width,
                   int (*measure)(void *, unsigned), void *ctx) {
    unsigned s = pos > BOOK_BACK ? pos - BOOK_BACK : 0, e;
    while (s && s < pos && t[s - 1] != '\n') ++s;
    if (s == pos && pos)
        for (s = pos > BOOK_BACK ? pos - BOOK_BACK : 0; (t[s] & 0xc0) == 0x80; ++s) {}
    while ((e = book_page(t, n, s, rows, width, measure, 0, ctx)) < pos && e > s) s = e;
    return s;
}

#ifndef PEQ_HOST
#define BOOK_WIN (32 << 10) /* the file's bytes held round the page */
#define BOOK_AHEAD (8 << 10)
#define BOOK_PX 20 /* the stock list font's size */
#define BOOK_LH 26 /* line pitch */
#define BOOK_X 24  /* the text's left edge, and its margin on the right */
#define BOOK_Y 6   /* the first line's top: ten lines clear the glass's corners */
#define BOOK_ROWS 10
#if IPOD
#define BOOK_TONE accent_tone(2)
#else
#define BOOK_TONE 0xaaaaaa
#endif
#define BOOKS_MAX 500 /* ponytail: ringnav navigates at most MAX_ENTRIES (512) rows */
#define MARKS_N 64    /* books whose page is kept, the most recently read first */

enum { LIST, READER };
enum { READY, PREPARING, BAD };
static struct {
    void *page, *list, *view, *reader, *sheet, *info;
    char **path; /* the books, by path */
    int n, screen, target, current, state, info_on;
    const char *file; /* the text read: the .txt, or the EPUB's in BOOK_DIR */
    char cache[48], tmp[48];
    unsigned key, size, pos, end, prev, base, len;
    unsigned char *buf;
    unsigned long thread;
    unsigned timer, poll;
    int running;
    volatile int finished, result, cancel;
    struct {
        unsigned key, pos;
    } marks[MARKS_N];
} bk __attribute__((section(".scratch")));

/* 1 for .txt, 2 for .epub, 3 for a video, by the extension from its dot; else 0. */
static int ext_kind(const char *dot) {
    if (!dot) return 0;
    for (const char *v = "mp4\0m4v\0mkv\0avi\0mov\0mpg\0"; *v; v += 4)
        if (!strcasecmp(dot + 1, v)) return 3;
    return !strcasecmp(dot + 1, "txt") ? 1 : !strcasecmp(dot + 1, "epub") ? 2 : 0;
}
/* ext_kind of a file name, not hidden. */
static int book_kind(const char *name) { return name[0] == '.' ? 0 : ext_kind(strrchr(name, '.')); }
/* Folder view's listing (playback.c ringnav_audio_extension): ext, from its dot, is a video's. */
int video_ext(const char *ext) { return ext_kind(ext) == 3; }

/* dir's books, and with depth those of its subfolders, as full paths. */
static void scan(const char *dir, int depth) {
    void *d = opendir(dir);
    char path[600];
    for (struct dirent *e; d && bk.n < BOOKS_MAX && (e = readdir(d));) {
        if (e->d_name[0] == '.') continue;
        tk_snprintf(path, sizeof path, "%s/%s", dir, e->d_name);
        int kind = book_kind(e->d_name);
        if ((e->d_type == 8 || !e->d_type) && kind && kind != 3) {
            if ((bk.path[bk.n] = strdup(path))) ++bk.n;
        } else if ((e->d_type == 4 || !e->d_type) && depth)
            scan(path, 0);
    }
    if (d) closedir(d);
}

/* path's file name without its extension, into out (sizeof 256). */
static const char *title(const char *path, char *out) {
    tk_snprintf(out, 256, "%s", strrchr(path, '/') + 1); /* a full path */
    char *dot = strrchr(out, '.');
    if (dot && dot != out) *dot = 0;
    return out;
}

/* The file round pos into the window: from 8 KB before it, kept while the page has 4 KB behind it
 * and BOOK_AHEAD ahead, or the file's start or end. */
static void window(unsigned pos) {
    if (bk.len && (!bk.base || pos >= bk.base + 4096) &&
        (bk.base + bk.len >= bk.size || pos + BOOK_AHEAD <= bk.base + bk.len))
        return;
    void *f = fopen(bk.file, "rb");
    bk.base = pos > BOOK_AHEAD ? pos - BOOK_AHEAD : 0;
    bk.len = f && !fseek(f, (long)bk.base, 0) ? fread(bk.buf, 1, BOOK_WIN, f) : 0;
    if (f) fclose(f);
}

/* The bottom caption: how far in and the title when Centre turned it on, or why there is no
 * page. */
static void info(void) {
    char name[256], caption[300];
    if (!bk.info) return;
    unsigned pct = bk.pos / (bk.size / 100 + 1); /* how far in, 0-99 */
    if (bk.state)
        tk_snprintf(caption, sizeof caption, "%s",
                    bk.state == BAD ? "无法打开此书籍" : "准备中…");
    else
        tk_snprintf(caption, sizeof caption, "%u%%  %s", pct, title(bk.path[bk.current], name));
    if (text_differs(bk.info, caption)) {
        widget_set_text_utf8(bk.info, caption);
        widget_set_prop_int(bk.info, "xoffset", 0); /* a long caption's scroll would shift this one left */
    }
    widget_set_visible(bk.info, bk.info_on || bk.state, 0);
}

/* key's place first in BOOK_MARKS: a book's byte offset, or a video's second. */
static void mark(unsigned key, unsigned pos) {
    int k = 0;
    while (k < MARKS_N - 1 && bk.marks[k].key != key) ++k;
    for (; k; --k) bk.marks[k] = bk.marks[k - 1];
    bk.marks[0].key = key, bk.marks[0].pos = pos;
    BLOB_IO(BOOK_MARKS, bk.marks, 1);
}

static void marks_save(void) {
    if (bk.screen == READER && !bk.state) mark(bk.key, bk.pos);
}

/* The text file is ready: open it at the page last read. */
static void load(void) {
    long size = file_size(bk.file);
    bk.size = size > 0 ? (unsigned)size : 0;
    bk.state = bk.size ? READY : BAD;
    bk.pos = bk.end = bk.prev = bk.len = 0;
    for (int k = 0; k < MARKS_N; ++k)
        if (bk.marks[k].key == bk.key && bk.marks[k].pos < bk.size) bk.pos = bk.marks[k].pos;
    window(bk.pos);
}

static void *worker(void *unused) {
    bk.result = book_convert(bk.path[bk.current], bk.tmp, &bk.cancel);
    bk.finished = 1;
    return 0;
}

static void stop_worker(void) {
    if (worker_stop(bk.thread, &bk.running, &bk.cancel, &bk.poll)) unlink(bk.tmp);
}

static int poll(const void *unused) {
    if (!bk.finished) return 8; /* RET_REPEAT */
    bk.poll = 0;
    pthread_join(bk.thread, 0);
    bk.running = 0;
    if (bk.result && !rename(bk.tmp, bk.cache))
        load();
    else
        unlink(bk.tmp), bk.state = BAD;
    info();
    widget_invalidate_force(bk.page, 0);
    return 0;
}

/* Book target: its text, or an EPUB's made first on the worker. */
static int to_reader(const void *unused) {
    bk.timer = 0;
    const char *path = bk.path[bk.current = bk.target];
    bk.key = fnv(FNV_SEED, (const unsigned char *)path);
    bk.key |= !bk.key;
    bk.screen = READER;
    bk.info_on = 0;
    widget_set_visible(bk.list, 0, 0);
    widget_set_visible(bk.reader, 1, 0);
    bk.file = path;
    bk.state = READY;
    if (book_kind(path) == 2) {
        tk_snprintf(bk.cache, sizeof bk.cache, BOOK_DIR "/%08x.txt", bk.key);
        tk_snprintf(bk.tmp, sizeof bk.tmp, BOOK_DIR "/%08x.tmp", bk.key);
        bk.file = bk.cache;
        if (access(bk.cache, 0)) {
            mkdir(BOOK_DIR, 0755);
            bk.finished = bk.cancel = 0;
            bk.running = !pthread_create(&bk.thread, 0, worker, 0);
            bk.state = bk.running ? PREPARING : BAD;
            if (bk.running) bk.poll = timer_add(poll, 0, 250);
        }
    }
    if (bk.state == READY) load();
    info();
    widget_invalidate_force(bk.page, 0);
    return 0;
}

static int open_book(void *ctx, void *event) {
    bk.target = (int)(long)ctx;
    rearm(&bk.timer, to_reader, 0);
    return 0;
}

static int leave(const void *unused) {
    bk.timer = 0;
    if (bk.screen != READER) {
        navigator_back();
        return 0;
    }
    marks_save();
    stop_worker();
    bk.screen = LIST;
    bk.state = READY;
    widget_set_visible(bk.reader, 0, 0);
    widget_set_visible(bk.list, 1, 0);
    ringnav_select(bk.view, bk.current, bk.n);
    widget_invalidate_force(bk.page, 0);
    return 0;
}

/* Return: reader -> books -> Local Music. */
static int keyup(void *ctx, void *event) {
    return return_key(event, &bk.timer, leave);
}

/* ringnav(): the reader takes the wheel (a page either way) and Centre (the caption). */
int books_key(void *top, unsigned key) {
    if (!bk.page || top != bk.page || bk.screen != READER) return 0;
    if (key == KEY_CENTER)
        bk.info_on = !bk.info_on;
    else if (bk.state)
        return 1;
    else if (key == KEY_NEXT && bk.end > bk.pos && bk.end < bk.size)
        bk.pos = bk.end;
    else if (key == KEY_PREV && bk.prev < bk.pos)
        bk.pos = bk.prev;
    window(bk.pos);
    info();
    widget_invalidate_force(bk.page, 0);
    return 1;
}

static int closed(void *ctx, void *event) {
    marks_save();
    stop_worker();
    stop_timer(&bk.timer);
    for (int k = 0; k < bk.n; ++k) free(bk.path[k]);
    free(bk.path);
    free(bk.buf);
    memset(&bk, 0, sizeof bk);
    return 0;
}

/* In 1/16 px, so fractional advances add up as drawn. */
static int measure(void *canvas, unsigned c) {
    return (int)(16 * canvas_measure_text(canvas, &c, 1));
}

static void draw_line(void *canvas, int row, const unsigned *s, int n) {
    canvas_draw_text(canvas, s, n, BOOK_X, BOOK_Y + row * BOOK_LH);
}

/* ringnav_paint calls this for every widget after stock painted it: the reader's sheet gets the
 * page, and the page before it is found while the font is set. */
void books_paint(void *w, void *canvas) {
    if (!bk.page || w != bk.sheet || bk.state || bk.pos < bk.base) return;
    unsigned color = (unsigned)I(P(canvas, CANVAS_LCD), LCD_TEXT_COLOR);
    canvas_set_font(canvas, (void *)0, BOOK_PX); /* the system default font */
    canvas_set_text_color(canvas, 0xffffffff);
    int width = 16 * (375 - 2 * BOOK_X);
    bk.end = bk.base + book_page(bk.buf, bk.len, bk.pos - bk.base, BOOK_ROWS, width, measure,
                                 draw_line, canvas);
    bk.prev =
        bk.base + book_back(bk.buf, bk.len, bk.pos - bk.base, BOOK_ROWS, width, measure, canvas);
    stock_canvas_set_text_color(canvas, color);
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    int y = BOOK_Y + BOOK_ROWS * BOOK_LH + 6, span = 375 - 16;
    unsigned end = bk.end, size = bk.size;
    while (size > ~0u / (unsigned)span) end >>= 1, size >>= 1;
    int done = size ? (int)(end * (unsigned)span / size) : 0;
    canvas_set_fill_color(canvas, rgba(0x2b2b2b, 255));
    canvas_fill_rect(canvas, 8, y, span, 2);
    canvas_set_fill_color(canvas, rgba(BOOK_TONE, 255));
    canvas_fill_rect(canvas, 8, y, done > span ? span : done, 2);
    stock_canvas_set_fill_color(canvas, fill);
}

/* Videos (docs/internals.md#videos): patch/video.c draws on /dev/fb0 and plays the sound while
 * demo runs on without painting (ringnav_wm_paint) or input (ringnav_input); a key's release
 * reaches it as a datagram on its socket. */
#define VIDEO_BIN "/usr/bin/q2video"
#define VIDEO_SOCK "/tmp/q2video.sock" /* video.c Q2VIDEO_SOCK */
#define VIDEO_PLACE "/tmp/q2video.place" /* video.c Q2VIDEO_PLACE: the second to start at, then left */
#define VIDEO_PAUSE_QUIT_MS 30000 /* paused this long, no key: quit as Return, so it sleeps */
static struct {
    int paused, volume, gain, pid, sock, seek; /* seek: the wheel seeks, since seek_at (time_now_ms) */
    unsigned polled, control_at, seek_at, key, at; /* key: the video's in BOOK_MARKS, at its second there */
    unsigned paused_at; /* time_now_ms when the pause began, 0 playing */
} vid __attribute__((section(".scratch")));

int video_on(void) { return vid.pid; }

/* A USB DAC's volume is in software, hciplayer's as Bluetooth's, only when the USB volume is
 * variable (g_usbvol_mode) and the DAC has no volume control of its own: mclUsbAudioSetVol then
 * finds none (USB_MIXER -2) and tells hciplayer {mcl-softvolflag\1}. Otherwise the DAC applies it. */
static int usb_soft(void) { return g_usbvol_mode && I(USB_MIXER, 0) == -2; }

/* The output in use for q2video (here, radio.c) and librespot's sink (spotify.c): 0 for the
 * headphone DAC, which keeps the volume set. Bluetooth's is hciplayer's soft volume, so the helper
 * gets g_volume in vol (5 bytes) to apply it the same way, on hciplayer's own plug:bluealsa (the
 * device demo writes to /mnt/data/asound.conf). A USB DAC is "usb", which the helper finds in
 * /proc/asound/cards (plughw, for its rate): with g_volume when its volume is soft, else "h" and
 * the volume, which the helper only shows. */
const char *output_device(char *vol) {
    int way = mclGetOutputWay();
    tk_snprintf(vol, 5, way == 2 && !usb_soft() ? "h%u" : "%u",
                way == 1 ? headset_gain() : g_volume);
    return way == 1 ? "plug:bluealsa" : way == 2 ? "usb" : 0;
}

/* The headphone output readied as stock's player_start readies it before each track (headset mode,
 * DAC powered, PCM mode, unmuted, the volume); Bluetooth's way left alone. Shared with spotify.c. */
void dac_take(void) {
    if (g_headset_output < 2)
        config_outputchannel(g_headset_output, 2);
    else if (I(g_dacoff_time, 0) < 0)
        mclSetDacPwr(1);
    mclSetPcmMode();
    mclSetMute(0);
    device_set_volume(g_volume, 1);
}

/* q2video on output_device, else the headphone DAC's plughw:0,0 (the only card), readied first, for
 * file or, with radio, Internet Radio's url (radio.c): its pid, 0 when it did not start. */
int video_start(const char *file, int radio) {
    char vol[5];
    const char *out = output_device(vol);
    player_stop(); /* hciplayer holds the PCM even paused */
    if (!out) dac_take(); /* without it the PCM takes the sound and the jack stays silent */
    int pid = fork();
    if (!pid) {
        const char *dev = out ? out : "plughw:0,0", *v = out ? vol : (char *)0;
        if (radio)
            execl(VIDEO_BIN, VIDEO_BIN, "-r", dev, file, v, (char *)0);
        else
            execl(VIDEO_BIN, VIDEO_BIN, dev, file, v, (char *)0);
        child_exit(127);
    }
    return pid > 0 ? pid : 0;
}

/* path played from its saved place, from the Videos list or Folder view (books_video). */
static void video_play(const char *path) {
    if (vid.pid) return;
    headset_manual();
    spot_yield(); /* librespot and radio hold the PCM too, as for local music */
    radio_stop();
    if (!bk.page) BLOB_IO(BOOK_MARKS, bk.marks, 0); /* Folder view: no list read them */
    vid.key = fnv(FNV_SEED, (const unsigned char *)path);
    vid.key |= !vid.key;
    vid.at = 0;
    for (int k = 0; k < MARKS_N; ++k)
        if (bk.marks[k].key == vid.key) vid.at = bk.marks[k].pos;
    BLOB_IO(VIDEO_PLACE, vid.at, 1);
    vid.pid = video_start(path, 0);
    vid.seek = vid.paused = 0;
    vid.paused_at = 0;
    vid.volume = g_volume; /* the helper starts at output_device's level: nothing to send, no bar */
    vid.gain = headset_gain();
    unlink("/tmp/q2video.state");
    if (vid.pid) vid.sock = socket(1, 1, 0); /* AF_UNIX, SOCK_DGRAM (MIPS numbering) */
}
/* Folder view's file (navigation.c ringnav_folder_click): a video plays as from the Videos list;
 * 0 for anything else, left to stock. */
int books_video(const char *path) {
    const char *name = path ? strrchr(path, '/') : 0;
    if (!name || book_kind(name + 1) != 3) return 0;
    video_play(path);
    return 1;
}

/* the helper's soft volume and gain (its bar), and what it was last sent */
static void video_sync(void) {
    char v[4] = { 'v', (char)g_volume, 'g', (char)headset_gain() };
    dgram_send(&vid.sock, VIDEO_SOCK, v, 2);
    dgram_send(&vid.sock, VIDEO_SOCK, v + 2, 2);
    vid.volume = g_volume;
    vid.gain = headset_gain();
}

/* ringnav_sleep, every UI loop pass: the helper's end, and meanwhile no screen, standby or DAC
 * power-off timeout (on_wm_idle_status, check_dacoff_state). A pause of VIDEO_PAUSE_QUIT_MS quits
 * as Return does (the place kept), so a pause from a dropped headset can't hold them forever.
 * ponytail: input goes to the helper, so the pause can't just let the screen time out under it;
 * a hold that lets a paused video go dark needs the wake path to reach stock while q2video runs. */
void video_poll(void) {
    int status;
    if (!vid.pid) return;
    if (!waitpid(vid.pid, &status, 1)) { /* WNOHANG: still playing */
        int paused;
        if (time_now_ms() - vid.polled >= 400) {
            vid.polled = time_now_ms();
            if (time_now_ms() - vid.control_at >= 1000 &&
                BLOB_IO("/tmp/q2video.state", paused, 0) && (paused == 0 || paused == 1))
                vid.paused = paused;
            if (vid.volume != g_volume || vid.gain != headset_gain()) video_sync();
            if (!vid.paused) vid.paused_at = 0;
            else if (!vid.paused_at) vid.paused_at = vid.polled ?: 1;
            else if (vid.polled - vid.paused_at >= VIDEO_PAUSE_QUIT_MS)
                dgram_send(&vid.sock, VIDEO_SOCK, "q", 1); /* again each poll until it ends */
        }
        reset_poweroptions_timer(1, 1, 1);
        if (I(g_dacoff_time, 0) > 0) I(g_dacoff_time, 0) = 0;
        if (vid.seek && time_now_ms() - vid.seek_at >= SCRUB_MS) vid.seek = 0; /* back on volume */
        return;
    }
    close(vid.sock);
    vid.pid = 0;
    unsigned at;
    /* where it stopped; a video never resumed takes no slot from the books. From Folder view no
     * page holds the marks, so they are read again first: mark writes them whole. */
    if (BLOB_IO(VIDEO_PLACE, at, 0) && (at || vid.at)) {
        if (!bk.page) BLOB_IO(BOOK_MARKS, bk.marks, 0);
        mark(vid.key, at);
    }
    widget_invalidate_force(window_manager(), 0);
}

/* Return quits, Play/Pause pauses, the side buttons seek. The wheel is the volume, as on Now
 * Playing; Centre toggles it to seeking, which ends SCRUB_MS after the last tick (video_poll). The
 * volume steps as stock's volume_dialog keys (0x4a2044): 1 a tick, up to 100 and g_maxvolume,
 * through device_set_volume (the DAC's, or hciplayer's for Bluetooth and a soft USB volume) and
 * saved; the helper gets it for that soft gain and its bar. */
void video_key(unsigned key) {
    vid.paused_at = 0; /* a key restarts a pause's VIDEO_PAUSE_QUIT_MS */
    if (key == KEY_PLAY || key == KEY_FWD_BTN || key == KEY_BACK_BTN || key == KEY_RETURN)
        headset_manual();
    int wheel = key == KEY_NEXT || key == KEY_PREV, v = g_volume + (key == KEY_NEXT ? 1 : -1);
    if (key == KEY_CENTER) vid.seek = !vid.seek;
    if (key == KEY_CENTER || (wheel && vid.seek)) vid.seek_at = time_now_ms();
    if (wheel && !vid.seek && v >= 0 && (key == KEY_PREV || (v <= 100 && v <= g_maxvolume))) {
        g_volume = (unsigned char)v;
        device_set_volume(v, 1);
        write_int_config(v, "PLAYSET", "VOLUME");
    }
    char c = key == KEY_RETURN                                      ? 'q'
             : key == KEY_PLAY                                      ? 'p'
             : key == KEY_FWD_BTN || (key == KEY_NEXT && vid.seek)  ? 'f'
             : key == KEY_BACK_BTN || (key == KEY_PREV && vid.seek) ? 'b'
             : key == KEY_CENTER && vid.seek                        ? 's'
             : wheel || key == KEY_CENTER                           ? 'v'
                                                                    : 0;
    if (c == 'v') video_sync();
    else if (c) dgram_send(&vid.sock, VIDEO_SOCK, &c, 1);
    if (c == 'p') {
        vid.paused = !vid.paused;
        vid.control_at = time_now_ms();
    }
}

/* Local Music's Books row (navigation.c media_click): the books, by path. */
void books_open(const char *root) {
    if (bk.page) return;
    void *page = bk.page = page_open("books_page", closed, keyup);
    if (!page) return;
    BLOB_IO(BOOK_MARKS, bk.marks, 0);
    void *f = widget_factory();
    int h = widget_get_prop_int(page, "h", 290);
    bk.list = widget_factory_create_widget(f, "view", page, 0, 0, 375, h);
    bk.reader = widget_factory_create_widget(f, "view", page, 0, 0, 375, h);
    bk.sheet = widget_factory_create_widget(f, "view", bk.reader, 0, 0, 375, h);
    bk.info = bottom_caption(bk.reader, h);
    widget_set_visible(bk.reader, 0, 0);
    bk.buf = calloc(BOOK_WIN, 1);
    bk.path = calloc(BOOKS_MAX, sizeof *bk.path);
    if (bk.buf && bk.path) scan(root, 1);
    if (bk.n) qsort(bk.path, (unsigned)bk.n, sizeof *bk.path, by_string);
    bk.view = page_list(page, bk.list, 0, bk.n ? "书籍" : "暂无书籍", bk.n, 48);
    char name[256];
    for (int k = 0; k < bk.n; ++k)
        page_row_detail_into(bk.view, k, title(bk.path[k], name), 0, open_book);
}
int video_status(void) { return vid.pid ? (vid.paused ? 3 : 2) : 0; }
unsigned video_session(void) { return (unsigned)vid.pid; }
void video_set_playing(int playing) {
    if (vid.pid) {
        dgram_send(&vid.sock, VIDEO_SOCK, playing ? "r" : "a", 1);
        vid.paused = !playing;
        vid.control_at = time_now_ms();
    }
}

#endif
