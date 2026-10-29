#include "peq.h"

/* The audio demuxer's original 16-byte private prefix stays at the same offsets. */
typedef struct {
    int format, pad;
    double next_pts;
    unsigned checked, start, skip, padding, rate, channels, held, unit, seeked, tag_skipped, guarded;
    unsigned char *tail;
} mp3_private;

/* Raw MPlayer decoders also emit the Xing frame. FFmpeg's mp3 demuxer removes it instead.
 * Delay/padding are per channel: https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/mp3dec.c */
int mp3_gapless(const unsigned char *h, unsigned n, unsigned *start, unsigned *padding,
                unsigned *rate, unsigned *channels) {
    static const unsigned rates[] = {44100, 48000, 32000};
    static const unsigned bitrates[2][15] = {
        {0,32,40,48,56,64,80,96,112,128,160,192,224,256,320},
        {0,8,16,24,32,40,48,56,64,80,96,112,128,144,160}
    };
    if (n < 4 || h[0] != 255 || (h[1] & 224) != 224) return 0;
    unsigned version = h[1] >> 3 & 3, r = h[2] >> 2 & 3, br = h[2] >> 4;
    if (version == 1 || (h[1] & 6) != 2 || r == 3 || !br || br == 15) return 0;
    unsigned hz = rates[r] >> (version == 3 ? 0 : version == 2 ? 1 : 2);
    unsigned spf = version == 3 ? 1152 : 576, mono = (h[3] & 192) == 192;
    unsigned size = (version == 3 ? 144000 : 72000) * bitrates[version != 3][br] / hz + (h[2] >> 1 & 1);
    if (n < size) return 0;
    unsigned at = 4 + (version == 3 ? (mono ? 17 : 32) : (mono ? 9 : 17));
    /* LAME keeps the fixed Xing offset even on CRC-protected frames; accept both layouts. */
    if (size < at + 8) return 0;
    if (memcmp(h + at, "Xing", 4) && memcmp(h + at, "Info", 4)) {
        if (h[1] & 1 || size < at + 10) return 0;
        at += 2;
        if (memcmp(h + at, "Xing", 4) && memcmp(h + at, "Info", 4)) return 0;
    }
    unsigned flags = h[at + 7];
    if (h[at + 4] || h[at + 5] || h[at + 6] || flags & ~15u || !(flags & 1)) return 0;
    at += 8;
    if (size < at + 4) return 0;
    unsigned frames = (unsigned)h[at] << 24 | h[at+1] << 16 | h[at+2] << 8 | h[at+3];
    at += 4 + (flags & 2 ? 4 : 0) + (flags & 4 ? 100 : 0) + (flags & 8 ? 4 : 0);
    if (!frames || size < at + 36 || (h[at + 9] >> 4) ||
        (memcmp(h + at, "LAME", 4) && memcmp(h + at, "Lavc", 4) && memcmp(h + at, "Lavf", 4))) return 0;
    unsigned delay = (unsigned)h[at+21] << 4 | h[at+22] >> 4;
    unsigned pad = (unsigned)(h[at+22] & 15) << 8 | h[at+23];
    if ((!delay && !pad) || delay == 4095 || pad == 4095 || (unsigned long long)frames * spf <= delay + pad) return 0;
    *start = spf + delay + 529;
    /* shortcut: padding below decoder delay cannot recover the missing tail; flush an extra
     * MPEG frame if support for specially split continuous MP3 streams is needed. */
    *padding = pad > 529 ? pad - 529 : 0;
    *rate = hz;
    *channels = mono ? 1 : 2;
    return 1;
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
static mp3_private *mp3_state(void *d) {
    mp3_private *p = d && AT(d, 0, unsigned) == 0x89fd90u ? AT(d, 0xc70, mp3_private *) : 0;
    return p && p->format == 1 ? p : 0;
}

/* Called in place of demux_audio_open's malloc(16); stock frees the same pointer. */
void *mp3_private_alloc(unsigned size) {
    (void)size;
    return calloc(1, sizeof(mp3_private));
}

af_data *mp3_filter(void *filters, af_data *data, void *sh) {
    void *ds = AT(sh, 0, void *), *d = ds ? AT(ds, 0x58, void *) : 0;
    mp3_private *p = mp3_state(d);
    if (p && !p->checked) {
        unsigned char h[2048];
        p->checked = 1;
        void *s = AT(d, 0x20, void *);
        if (s) {
            int fd = AT(s, 0x14, int), got = 0;
            long long back = lseek64(fd, 0, 1), at = AT(d, 0x10, long long);
            if (back >= 0) {
                if (lseek64(fd, at, 0) == at) got = read(fd, h, sizeof h);
                lseek64(fd, back, 0);
            }
            if (got > 0 && mp3_gapless(h, got, &p->start, &p->padding, &p->rate, &p->channels))
                p->skip = p->seeked == 1 ? 0 : p->start - (p->tag_skipped ? (p->rate >= 32000 ? 1152 : 576) : 0);
        }
    }
    if (p && p->start) mp3_trim(p, data);
    return ((af_data *(*)(void *, af_data *))0x44d350u)(filters, data);
}

void mp3_gapless_seek(void *d, int at_start) {
    mp3_private *p = mp3_state(d);
    if (p) {
        p->held = 0;
        p->guarded = 0;
        p->seeked = at_start ? 2 : 1;
        /* Stock demux_audio_seek makes the next fill discard one frame to resync. */
        p->tag_skipped = p->seeked == 2 && *(volatile int *)0xb152b4u;
        p->skip = p->start && p->seeked == 2 ? p->start - (p->tag_skipped ? (p->rate >= 32000 ? 1152 : 576) : 0) : 0;
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

void mp3_close(void *d) {
    mp3_private *p = mp3_state(d);
    if (p) free(p->tail);
    ((void (*)(void *))0x48b7e4u)(d);
}
#endif
