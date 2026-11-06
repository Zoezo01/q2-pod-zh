#include "peq.h"

/* The audio demuxer's original 16-byte private prefix stays at the same offsets. */
typedef struct {
    int format, pad;
    double next_pts;
    unsigned checked, start, skip, padding, rate, channels, held, unit, seeked, guarded;
    unsigned char *tail;
    long long audio_end;
} mp3_private;

/* Raw MPlayer decoders also emit the Xing frame. FFmpeg's mp3 demuxer removes it instead.
 * Delay/padding are per channel: https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/mp3dec.c
 * ponytail: a file with no extension and no iTunSMPB (no tag, VBRI) keeps 1624 to 2176 samples of delay and padding
 * at each boundary, nothing says how many to cut. docs/internals.md lists these. */
static int mp3_named(const unsigned char *n, const char *name) {
    for (unsigned i = 0; i < 4; ++i) if ((n[i] | 32) != name[i]) return 0;
    return 1;
}

static unsigned mp3_crc16(const unsigned char *p, unsigned n) {
    unsigned c = 0;
    while (n--) {
        c ^= *p++;
        for (unsigned i = 0; i < 8; ++i) c = c & 1 ? c >> 1 ^ 0xa001 : c >> 1;
    }
    return c;
}

static int mp3_extension(const unsigned char *h, unsigned size, unsigned at, unsigned spf,
                         unsigned *start, unsigned *padding) {
    unsigned flags = h[at + 7];
    if (h[at + 4] || h[at + 5] || h[at + 6] || flags & ~15u || !(flags & 1)) return 0;
    at += 8;
    if (size < at + 4) return 0;
    unsigned frames = (unsigned)h[at] << 24 | h[at+1] << 16 | h[at+2] << 8 | h[at+3];
    at += 4 + (flags & 2 ? 4 : 0) + (flags & 4 ? 100 : 0) + (flags & 8 ? 4 : 0);
    if (!frames || size < at + 36) return 0;
    /* LAME's and FFmpeg's names in any case: an encoder really writes Lame3.100, its CRC right for that spelling, and
     * FFmpeg's exact list misses it. Any other name (L3.99r, GOGO) has to prove its fields with the tag's CRC-16.
     * Printable either way, so a tag without the extension (zeros, or whatever else a tool left there) isn't read as
     * delays, and not text all through the 12 field bytes that follow, which is a longer name. */
    unsigned text = 0;
    for (unsigned i = 0; i < 4; ++i) if (h[at + i] < 32 || h[at + i] > 126) return 0;
    for (unsigned i = 9; i < 21; ++i) text += h[at + i] >= 32 && h[at + i] <= 126;
    if (text == 12) return 0;
    if (!mp3_named(h + at, "lame") && !mp3_named(h + at, "lavc") && !mp3_named(h + at, "lavf") &&
        mp3_crc16(h, at + 34) != ((unsigned)h[at + 34] << 8 | h[at + 35])) return 0;
    unsigned delay = (unsigned)h[at+21] << 4 | h[at+22] >> 4;
    unsigned pad = (unsigned)(h[at+22] & 15) << 8 | h[at+23];
    if ((!delay && !pad) || delay == 4095 || pad == 4095 || (unsigned long long)frames * spf <= delay + pad) return 0;
    *start = spf + delay + 529;
    /* shortcut: padding below decoder delay cannot recover the missing tail; flush an extra
     * MPEG frame if support for specially split continuous MP3 streams is needed. */
    *padding = pad > 529 ? pad - 529 : 0;
    return 1;
}

/* The first frame's size, 0 unless h holds the whole of a Layer III frame. */
static unsigned mp3_frame(const unsigned char *h, unsigned n, unsigned *hz, unsigned *spf, unsigned *mono) {
    static const unsigned rates[] = {44100, 48000, 32000};
    static const unsigned bitrates[2][15] = {
        {0,32,40,48,56,64,80,96,112,128,160,192,224,256,320},
        {0,8,16,24,32,40,48,56,64,80,96,112,128,144,160}
    };
    if (n < 4 || h[0] != 255 || (h[1] & 224) != 224) return 0;
    unsigned version = h[1] >> 3 & 3, r = h[2] >> 2 & 3, br = h[2] >> 4;
    if (version == 1 || (h[1] & 6) != 2 || r == 3 || !br || br == 15) return 0;
    *hz = rates[r] >> (version == 3 ? 0 : version == 2 ? 1 : 2);
    *spf = version == 3 ? 1152 : 576;
    *mono = (h[3] & 192) == 192;
    unsigned size = (version == 3 ? 144000 : 72000) * bitrates[version != 3][br] / *hz + (h[2] >> 1 & 1);
    return n < size ? 0 : size;
}

/* Where a Xing, Info or VBRI name is in the first frame, 0 if none. LAME keeps the fixed Xing offset even on
 * CRC-protected frames; accept both layouts. VBRI has one place. */
static unsigned mp3_tag(const unsigned char *h, unsigned size, unsigned spf, unsigned mono) {
    unsigned at = 4 + (spf == 1152 ? (mono ? 17 : 32) : (mono ? 9 : 17));
    if (size >= at + 8 && (!memcmp(h + at, "Xing", 4) || !memcmp(h + at, "Info", 4))) return at;
    if (!(h[1] & 1) && size >= at + 10 && (!memcmp(h + at + 2, "Xing", 4) || !memcmp(h + at + 2, "Info", 4))) return at + 2;
    return size >= 42 && !memcmp(h + 36, "VBRI", 4) && !h[40] && h[41] == 1 ? 36 : 0;
}

/* Side info all zeros: no granule has a bit of main data, so a tag frame with it has no audio. */
static int mp3_silent(const unsigned char *h, unsigned tag) {
    for (unsigned i = h[1] & 1 ? 4 : 6; i < tag; ++i) if (h[i]) return 0;
    return 1;
}

/* 1: the extension's trim. 2: no delays to read, but the frame itself is no audio: stock plays it as spf samples of
 * silence, where FFmpeg drops it, and mpg123 too for Xing and Info. */
int mp3_gapless(const unsigned char *h, unsigned n, unsigned *start, unsigned *padding,
                unsigned *rate, unsigned *channels) {
    unsigned hz, spf, mono, size = mp3_frame(h, n, &hz, &spf, &mono);
    unsigned tag = size ? mp3_tag(h, size, spf, mono) : 0;
    if (!tag) return 0;
    int found = 1;
    if (h[tag] == 'V' || !mp3_extension(h, size, tag, spf, start, padding)) {
        if (!mp3_silent(h, tag)) return 0;
        *start = spf;
        *padding = 0;
        found = 2;
    }
    *rate = hz;
    *channels = mono ? 1 : 2;
    return found;
}

static int smpb_field(const char **v, unsigned long long *out) {
    const char *s = *v;
    unsigned long long x = 0;
    unsigned k = 0;
    while (*s == ' ') ++s;
    for (;; ++s, ++k) {
        unsigned c = (unsigned char)*s, d = c >= '0' && c <= '9' ? c - '0' : (c | 32) >= 'a' && (c | 32) <= 'f' ? (c | 32) - 'a' + 10 : 16;
        if (d == 16) break;
        x = x << 4 | d;
    }
    *out = x;
    *v = s;
    return k && k <= 16;
}

/* iTunes writes no extension but an iTunSMPB comment: reserved, priming, remainder, samples, in hex.
 * https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/mp3dec.c parses it since a1d17fdd28, with these refusals,
 * but takes priming as covering the decoder's delay and trims 529 samples less at the start and more at the end.
 * Measured here, on the one real file to hand (its gapless-itunes.mp3, iTunes 12.7.0.166), the audio stops at priming
 * + samples + 529, as with LAME's delay; the sum can't tell the readings apart. ponytail: an iTunes version that
 * really counted the decoder would come out 529 samples (12 ms) off at each boundary; docs/internals.md. */
int mp3_smpb(const unsigned char *h, unsigned n, const char *v, unsigned *start, unsigned *padding,
             unsigned *rate, unsigned *channels) {
    unsigned hz, spf, mono, size = mp3_frame(h, n, &hz, &spf, &mono);
    unsigned long long reserved, priming, remainder, samples, frames = 0;
    if (!size || !smpb_field(&v, &reserved) || !smpb_field(&v, &priming) || !smpb_field(&v, &remainder) ||
        !smpb_field(&v, &samples)) return 0;
    /* mp3_trim's tail buffer is sized from the remainder: as big as a LAME tag can ask for. */
    if (priming > 16384 || remainder > 4095 || !samples || samples >> 40) return 0;
    unsigned tag = mp3_tag(h, size, spf, mono);
    if (tag) {
        const unsigned char *c = h[tag] == 'V' ? h + 50 : h[tag + 7] & 1 ? h + tag + 8 : 0;
        if (!mp3_silent(h, tag)) return 0;
        if (c && size >= (unsigned)(c - h) + 4) frames = (unsigned)c[0] << 24 | c[1] << 16 | c[2] << 8 | c[3];
    }
    /* The tag's count leaves out the tag frame. Without a count, FFmpeg has nothing to check either. */
    if (frames && priming + samples + remainder != frames * spf) return 0;
    *start = (tag ? spf : 0) + priming + 529;
    *padding = remainder > 529 ? remainder - 529 : 0;
    *rate = hz;
    *channels = mono ? 1 : 2;
    return 1;
}

/* What the ID3v1 tag takes off a file's end, from its last n bytes t, and in *ape what all the tags do if an APEv2
 * footer there is sane and says it has a header, which the caller must find at that place. stock's demuxer scans a
 * tag for frames: non-JPEG bytes in APEv2 can decode as noise, and the trailing trim then cuts that, not the padding.
 * With a wrong size this would lose music, so a footer with nothing to confirm it is left alone: a false stop loses
 * music, a false pass only some noise. ponytail: not an APEv2 tag ahead of Lyrics3, which is rare. */
long long mp3_tail(const unsigned char *t, unsigned n, long long *ape) {
    long long cut = n >= 128 && !memcmp(t + n - 128, "TAG", 3) ? 128 : 0;
    *ape = 0;
    if (n < cut + 32) return cut;
    const unsigned char *f = t + n - cut - 32;
    unsigned version = (unsigned)f[8] | f[9] << 8 | f[10] << 16 | (unsigned)f[11] << 24;
    unsigned size = (unsigned)f[12] | f[13] << 8 | f[14] << 16 | (unsigned)f[15] << 24;
    if (!memcmp(f, "APETAGEX", 8) && version <= 2000 && size >= 32 && (f[23] & 0xa0) == 0x80)
        *ape = cut + (long long)size + 32;
    return cut;
}

/* Keep the last padding frames until another block arrives. They disappear at EOF, without
 * trusting a VBR seek's approximate sample position or the file's advertised frame count. */
static void mp3_trim(mp3_private *p, af_data *data) {
    if (data->len <= 0 || data->rate != (int)p->rate || data->nch != (int)p->channels ||
        data->bps < 1 || data->bps > 4 || data->len % (data->bps * data->nch)) return;
    unsigned unit = data->bps * data->nch, n = data->len;
    if (p->unit != unit) p->held = 0;
    p->unit = unit;
    unsigned skip = p->skip < n / unit ? p->skip : n / unit;
    if (!p->padding) {
        p->skip -= skip;
        data->audio = (unsigned char *)data->audio + skip * unit;
        data->len -= skip * unit;
        return;
    }
    /* Reserve both tail workspaces before holding any audio; later blocks cannot fail to grow. */
    if (!p->tail && !(p->tail = calloc(p->padding, 16))) { p->start = 0; return; }
    unsigned tail = p->padding * unit, held = p->held;
    unsigned char *audio = (unsigned char *)data->audio + skip * unit;
    unsigned char *saved = p->tail + p->padding * 8;
    p->skip -= skip;
    n -= skip * unit;
    unsigned emit = held + n > tail ? held + n - tail : 0;
    if (n >= tail) {
        memcpy(saved, audio + n - tail, tail);
        memmove(audio + held, audio, n - tail);
        if (held) memcpy(audio, p->tail, held);
        memcpy(p->tail, saved, tail);
    } else {
        if (emit) memcpy(saved, p->tail, emit);
        memmove(p->tail, p->tail + emit, held - emit);
        memcpy(p->tail + held - emit, audio, n);
        if (emit) memcpy(audio, saved, emit);
    }
    p->held = held + n - emit;
    data->len = emit;
    data->audio = audio;
}

#ifndef PEQ_HOST
#define AT(p, o, type) (*(type *)((char *)(p) + (o)))
unsigned id3_int(const unsigned char *p, int syncsafe);
unsigned id3_txxx(const unsigned char *f, unsigned n, char *out, unsigned cap);

/* The iTunSMPB text in the ID3v2 tags ahead of the audio, or 0. Frames are skipped by their headers, so a cover
 * costs a seek; at most 64 of them looked at. Only for a file with no extension. A COMM frame is a TXXX frame's
 * body with a language after the encoding. */
static const char *mp3_smpb_text(int fd, long long end, char *t, unsigned cap) {
    unsigned char b[10], f[512];
    unsigned budget = 64;
    long long at = 0;
    while (at + 10 <= end && lseek64(fd, at, 0) == at && read(fd, b, 10) == 10 && !memcmp(b, "ID3", 3) && b[3] >= 2 && b[3] <= 4) {
        unsigned v = b[3], flags = b[5], head = v == 2 ? 6 : 10, name = v == 2 ? 3 : 4;
        long long stop = at + 10 + id3_int(b + 6, 1);
        at += 10;
        if (v > 2 && flags & 0x40 && read(fd, b, 4) == 4) at += v == 4 ? id3_int(b, 1) : 4 + id3_int(b, 0);
        /* Unsynchronised tags, and 2.2's compressed ones, aren't laid out as their sizes say. */
        while (!(flags & 0x80) && !(v == 2 && flags & 0x40) && budget && at + head <= stop && lseek64(fd, at, 0) == at &&
               read(fd, b, head) == (int)head && b[0]) {
            unsigned n = v == 2 ? (unsigned)b[3] << 16 | b[4] << 8 | b[5] : id3_int(b + 4, v == 4);
            int comment = !memcmp(b, v == 2 ? "COM" : "COMM", name), txxx = !memcmp(b, v == 2 ? "TXX" : "TXXX", name);
            --budget;
            if ((comment ? n > 3 : txxx && n) && n <= sizeof f && (v == 2 || !b[9]) && read(fd, f, n) == (int)n) {
                if (comment) f[3] = f[0];
                if (id3_txxx(comment ? f + 3 : f, comment ? n - 3 : n, t, cap) > 9 && !memcmp(t, "iTunSMPB", 9)) return t + 9;
            }
            at += head + n;
        }
        at = stop + (v == 4 && flags & 0x10 ? 10 : 0);
    }
    return 0;
}

static mp3_private *mp3_state(void *d) {
    mp3_private *p = d && AT(d, 0, unsigned) == 0x89fd90u ? AT(d, 0xc70, mp3_private *) : 0;
    return p && p->format == 1 ? p : 0;
}

/* Called in place of demux_audio_open's malloc(16); stock frees the same pointer. */
void *mp3_private_alloc(unsigned size) {
    (void)size;
    return calloc(1, sizeof(mp3_private));
}

void lavf_trim(void *d, void *sh, af_data *data);
af_data *mp3_filter(void *filters, af_data *data, void *sh) {
    void *ds = AT(sh, 0, void *), *d = ds ? AT(ds, 0x58, void *) : 0;
    mp3_private *p = mp3_state(d);
    if (p && !p->checked) {
        unsigned char h[2048], t[160];
        p->checked = 1;
        void *s = AT(d, 0x20, void *);
        if (s) {
            int fd = AT(s, 0x14, int), got = 0, found = 0;
            long long back = lseek64(fd, 0, 1), at = AT(d, 0x10, long long);
            if (back >= 0) {
                if (lseek64(fd, at, 0) == at) got = read(fd, h, sizeof h);
                long long end = lseek64(fd, -(long long)sizeof t, 2);
                if (end >= 0 && read(fd, t, sizeof t) == sizeof t) {
                    long long eof = end + sizeof t, ape, cut = mp3_tail(t, sizeof t, &ape);
                    if (ape && ape < eof && lseek64(fd, eof - ape, 0) == eof - ape && read(fd, t, 8) == 8 &&
                        !memcmp(t, "APETAGEX", 8)) cut = ape;
                    if (eof - cut > at) p->audio_end = eof - cut;
                }
                if (got > 0) found = mp3_gapless(h, got, &p->start, &p->padding, &p->rate, &p->channels);
                if (got > 0 && found != 1 && at > 0) {
                    char text[256];
                    const char *smpb = mp3_smpb_text(fd, at, text, sizeof text);
                    if (smpb) mp3_smpb(h, got, smpb, &p->start, &p->padding, &p->rate, &p->channels);
                }
                lseek64(fd, back, 0);
            }
            if (p->start) p->skip = p->seeked == 1 ? 0 : p->start;
        }
    }
    if (p && p->start) mp3_trim(p, data);
    lavf_trim(d, sh, data);
    return ((af_data *(*)(void *, af_data *))0x44d350u)(filters, data);
}

void mp3_gapless_seek(void *d, int at_start) {
    mp3_private *p = mp3_state(d);
    if (p) {
        p->held = 0;
        p->guarded = 0;
        p->seeked = at_start ? 2 : 1;
        /* Stock's fill after a seek (flag 0xb152b4, 0x48c3b8) checks the next frame's header, goes back, and sizes
         * the first packet from the next frame (0x48c4d0): the tag frame, cut short if that one is shorter, loses
         * libmad its sync, and a rewind starts two frames late on VBR. The start needs no checking. */
        if (at_start) *(volatile int *)0xb152b4u = 0;
        p->skip = at_start ? p->start : 0;
    }
}

/* read_frame's demux_read_data (ds, buf, room). libmad decodes a frame only with MAD_BUFFER_GUARD
 * (8) bytes after it, and demux_audio's packets are whole frames, so at EOF stock drops the last
 * frame: the end of the track's audio, which the trailing trim then cut further, a tick into
 * the next track. Zeros after it once, as Rockbox and minimp3 do, decode it. */
int mp3_read(void *ds, unsigned char *buf, int room) {
    int got = ((int (*)(void *, unsigned char *, int))0x484c0cu)(ds, buf, room);
    mp3_private *p = got > 0 ? 0 : mp3_state(AT(ds, 0x58, void *));
    if (!p || p->guarded || room < 8) return got;
    p->guarded = 1;
    memset(buf, 0, 8);
    return 8;
}

/* demux_audio's fill (0x48c184, the descriptor's slot): the stream stops where the tail tags start. */
int mp3_fill(void *d, void *ds) {
    mp3_private *p = mp3_state(d);
    void *s = AT(d, 0x20, void *);
    if (p && p->audio_end && AT(s, 0x30, long long) + AT(s, 0x28, int) - AT(s, 0x2c, int) >= p->audio_end) return 0;
    return ((int (*)(void *, void *))0x48c184u)(d, ds);
}

void mp3_close(void *d) {
    mp3_private *p = mp3_state(d);
    if (p) free(p->tail);
    ((void (*)(void *))0x48b7e4u)(d);
}
#endif
