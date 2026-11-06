/* Vorbis comments read a field at a time (xiph.h). TagLib's own read of an Ogg file's comment
 * header (Ogg::File::packet(1), then XiphComment::parse) holds the whole packet, its pages joined
 * by doubling, and decodes each METADATA_BLOCK_PICTURE beside it: about 4 times the header, and
 * MusicBee keeps a FLAC's pictures there when it converts to Opus. A 20 MB header got demo
 * OOM-killed in the library scan. Here only the page headers and the wanted fields are read;
 * everything else is skipped with fseek. */
#include "xiph.h"
#include "peq.h"

static unsigned le32(const unsigned char *p) {
    return p[0] | p[1] << 8 | p[2] << 16 | (unsigned)p[3] << 24;
}

/* Where a comment list's read stands: f at its next left bytes, which end the list when end is set.
 * An Ogg file's list is its packet 1, read from the pages; packet is the one the next lacing value
 * belongs to. A FLAC block is a single run, already ended. err: the pages broke off first. */
typedef struct {
    void *f;
    unsigned packet, left, end, pages, err;
} xiph_in;

/* The next run of packet 1 (TagLib's packet(1)): 1 with left bytes of it at f, else 0, past its end
 * or with err set at EOF, a page without "OggS" and version 0, or OGG_PAGES_MAX pages. Lacing
 * values count the packets, as Ogg::Page does; a page's other packets are seeked past. */
static int ogg_next(xiph_in *o) {
    unsigned char h[27 + 255];
    while (!o->left) {
        if (o->end) return 0;
        if (++o->pages > OGG_PAGES_MAX || fread(h, 27, 1, o->f) != 1 || memcmp(h, "OggS", 5) ||
            fread(h + 27, 1, h[26], o->f) != h[26])
            return o->err = 1, 0;
        unsigned skip = 0;
        for (unsigned i = 0; i < h[26] && !o->end; ++i) {
            if (o->packet)
                o->left += h[27 + i];
            else
                skip += h[27 + i];
            if (h[27 + i] < 255 && o->packet++) o->end = 1;
        }
        if (fseek(o->f, (long)skip, 1)) return o->err = 1, 0;
    }
    return 1;
}

/* Past the list's runs while they total at most max bytes: how many it passed. */
static unsigned ogg_skip(xiph_in *o, unsigned max) {
    unsigned n = 0;
    do
        if (o->left && fseek(o->f, (long)o->left, 1))
            o->err = 1;
        else
            n += o->left, o->left = 0;
    while (!o->err && n <= max && ogg_next(o));
    return n;
}

/* The list's next n bytes into p, or past them when p is 0: 1 when the list held them. */
static int xiph_read(xiph_in *o, void *p, unsigned n) {
    for (unsigned k; n; n -= k, o->left -= k) {
        if (!o->left && !ogg_next(o)) return 0;
        k = n < o->left ? n : o->left;
        if (p ? fread(p, 1, k, o->f) != k : fseek(o->f, (long)k, 1) != 0) return 0;
        if (p) p = (char *)p + k;
    }
    return 1;
}

/* v's n bytes onto a text field, after " / " unless it is the first (Tag::joinTagValues); cut at a
 * whole character where XIPH_TEXT ends, and no more after that. */
static void join(char *text, unsigned short *used, const char *v, unsigned n) {
    unsigned u = *used, i = 0, sep = u ? 3 : 0;
    if (u >= XIPH_TEXT - 1) return;
    for (; u < XIPH_TEXT - 1 && i < sep + n; ++i) text[u++] = i < sep ? " / "[i] : v[i - sep];
    if (i < sep + n) {
        if (i >= sep && (v[i - sep] & 0xc0) == 0x80) { /* the next byte continues a character */
            while ((text[u - 1] & 0xc0) == 0x80) --u;
            --u;
        }
        *used = XIPH_TEXT;
    } else
        *used = u;
    text[u] = 0;
}

static const char *const keys[XIPH_TEXTS + XIPH_NUMBERS] = {
    "TITLE", "ARTIST", "ALBUM",       "GENRE",    "COMPOSER",   "ALBUMARTIST", "ALBUM ARTIST",
    "DATE",  "YEAR",   "TRACKNUMBER", "TRACKNUM", "DISCNUMBER", "DISC",        "MQAENCODER"
};

/* The comment list at o, rem bytes, into t, as XiphComment::parse reads it: the vendor string, the
 * count (none at all when 4 bytes each would overrun), then "KEY=value" comments until one runs
 * past the end. A key matches case aside (String::upper); a value ends at a zero byte, addField
 * keeps no empty one, and one that is not UTF-8 decodes empty. ponytail: UTF-8 is checked up to a
 * value's first zero byte, in its first XIPH_VALUE bytes. With raw, only the first "ALBUM ARTIST"
 * with a byte of value is taken, its first 0xff bytes copied as they are into raw, and the count
 * goes unchecked: flac_album_artist's rules before xiph.c, which albums are grouped by. */
static void xiph_parse(xiph_in *o, unsigned rem, xiph_tags *t, char *raw) {
    unsigned char b[XIPH_VALUE + 1];
    unsigned n, c;
    if (rem < 8 || !xiph_read(o, b, 4) || (c = le32(b)) > rem - 8 || !xiph_read(o, 0, c) ||
        !xiph_read(o, b, 4))
        return;
    rem -= 8 + c;
    for (n = !raw && le32(b) > rem / 4 ? 0 : le32(b); n-- && rem >= 4 && xiph_read(o, b, 4);) {
        if ((c = le32(b)) > (rem -= 4)) break;
        rem -= c;
        unsigned got = c < XIPH_VALUE ? c : XIPH_VALUE, eq = 0, k = 0;
        if (!xiph_read(o, b, got) || !xiph_read(o, 0, c - got)) break;
        while (eq < got && b[eq] != '=') ++eq;
        while (k < XIPH_TEXTS + XIPH_NUMBERS &&
               (eq != strlen(keys[k]) || strncasecmp((char *)b, keys[k], eq)))
            ++k;
        if (eq == got || k == XIPH_TEXTS + XIPH_NUMBERS) continue;
        if (raw) {
            if (k != XIPH_ALBUM_ARTIST || c == eq + 1) continue;
            n = c - eq - 1 > 0xff ? 0xff : c - eq - 1;
            memcpy(raw, b + eq + 1, n), raw[n] = 0;
            return;
        }
        unsigned end = got; /* a value cut short by the read ends before the character it cut */
        if (got < c)
            for (end = got - 3; (b[end] & 0xc0) == 0x80;) --end;
        b[end] = 0;
        char *v = (char *)b + eq + 1; /* String(ByteVector) ends at a zero byte */
        if (!*v || !utf8_latin1(v, 0)) continue;
        if (k < XIPH_TEXTS)
            join(t->text[k], &t->used[k], v, strlen(v));
        else if (!t->has[k - XIPH_TEXTS])
            t->has[k - XIPH_TEXTS] = 1, t->num[k - XIPH_TEXTS] = (int)strtol(v, 0, 10);
    }
}

static int pick(const xiph_tags *t, int a, int b, int none) {
    return t->has[a] ? t->num[a] : t->has[b] ? t->num[b] : none;
}

int ogg_tags(const char *path, xiph_tags *t) {
    static const char *const ext[] = { ".opus", ".ogg", ".oga" };
    const char *e = strrchr(path, '.');
    unsigned k = 0;
    while (e && k < 3 && strcasecmp(e, ext[k])) ++k;
    void *f = e && k < 3 ? fopen(path, "rb") : 0;
    if (!f) return 0;
    /* the first page, with its packet 0's start: OpusHead, or Vorbis's identification header */
    unsigned char h[27 + 255 + 16] = { 0 }, m[8];
    xiph_in o = { f, 0, 0, 0, 0, 0 };
    unsigned n = fread(h, 1, sizeof h, f) >= 27 && !memcmp(h, "OggS", 5) && !fseek(f, 0, 0)
                     ? ogg_skip(&o, OGG_TAGS_MAX)
                     : 0;
    const unsigned char *head = h + 27 + h[26];
    int big = n > OGG_TAGS_MAX;
    if (t) memset(t, 0, sizeof *t), t->size = n;
    if (big && t) {
        t->size = n += ogg_skip(&o, ~0u);
        int whole = !o.err; /* else TagLib finds no packet 1, so no tag */
        o = (xiph_in){ f, 0, 0, 0, 0, 0 };
        if (whole && !fseek(f, 0, 0) && xiph_read(&o, m, 7)) {
            if (!memcmp(m, "\3vorbis", 7))
                t->kind = 2, t->rate = memcmp(head, "\1vorbis", 7) ? 0 : (int)le32(head + 12);
            else if (xiph_read(&o, m + 7, 1) && !memcmp(m, "OpusTags", 8))
                t->kind = memcmp(head, "OpusHead", 8) ? 0 : 1, t->rate = 48000;
            else
                big = 0; /* ponytail: Ogg FLAC, Speex and the like keep TagLib's read */
        }
        if (t->kind) {
            xiph_parse(&o, n - (t->kind == 1 ? 8 : 7), t, 0);
            t->year = pick(t, XIPH_DATE, XIPH_YEAR, 0);
            t->track = pick(t, XIPH_TRACKNUM, XIPH_TRACKNUMBER, 0);
            t->disc = pick(t, XIPH_DISC, XIPH_DISCNUMBER, 1000);
            t->mqa = t->has[XIPH_MQAENCODER];
        }
    }
    fclose(f);
    return big;
}

void flac_album_artist(const char *path, char *out) {
    const char *ext = strrchr(path, '.');
    if (!ext || strcasecmp(ext, ".flac")) return;
    void *f = fopen(path, "rb");
    unsigned char h[4];
    if (!f) return;
    if (fread(h, 4, 1, f) == 1 && !memcmp(h, "fLaC", 4))
        while (fread(h, 4, 1, f) == 1) {
            unsigned left = (unsigned)h[1] << 16 | h[2] << 8 | h[3];
            if ((h[0] & 0x7f) == 4) { /* VORBIS_COMMENT: one run, the block */
                xiph_in o = { f, 1, left, 1, 0, 0 };
                xiph_parse(&o, left, 0, out);
                break;
            }
            if (h[0] & 0x80 || fseek(f, (long)left, 1)) break;
        }
    fclose(f);
}

int utf8_latin1(const char *s, char *latin1) {
    int latin = 2;
    for (const unsigned char *p = (const unsigned char *)s; *p;) {
        unsigned c = *p++, n = c < 0x80                ? 0
                               : c >= 0xc2 && c < 0xe0 ? 1
                               : c >= 0xe0 && c < 0xf0 ? 2
                               : c >= 0xf0 && c < 0xf5 ? 3
                                                       : 4;
        if (n == 4) return 0;
        unsigned v = n ? c & (0x3f >> n) : c;
        for (unsigned i = 0; i < n; ++i, ++p) {
            if ((*p & 0xc0) != 0x80) return 0;
            v = v << 6 | (*p & 63);
        }
        if ((n == 2 && (v < 0x800 || (v >= 0xd800 && v < 0xe000))) ||
            (n == 3 && (v < 0x10000 || v > 0x10ffff)))
            return 0;
        if (v > 0xff) latin = 1;
        /* UTF-16, as TagLib's String holds it: a surrogate pair past U+FFFF */
        unsigned unit[2] = { v > 0xffff ? 0xd800 | (v - 0x10000) >> 10 : v, 0xdc00 | (v & 0x3ff) };
        for (unsigned i = 0; latin1 && i < 1u + (v > 0xffff); ++i)
            if ((*latin1 = (char)unit[i]))
                ++latin1;
            else
                latin1 = 0;
    }
    if (latin1) *latin1 = 0;
    return latin;
}

int utf8_text(const char *s) { return utf8_latin1(s, 0) != 0; }
