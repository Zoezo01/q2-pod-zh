#include "peq.h"

_Static_assert(sizeof(af_data) == 24, "audio ABI");
_Static_assert(__builtin_offsetof(af_instance, setup) == 16, "setup ABI");
_Static_assert(__builtin_offsetof(af_instance, mul) == 40, "multiplier ABI");

/* DSD (the vendor's dsdiff driver: dsf, dff, SACD iso; or FFmpeg's ffdsd*) reaches the chain as integer PCM,
 * possibly DoP, whose bits must arrive unchanged: pass it through. Never detach (2): the insert's af_add
 * (0x44d098) returns the filter its reinit just freed, and the caller (0x4291ac) then calls into it.
 * Codec lookup: docs/internals.md. */
#ifdef PEQ_HOST
extern char *peq_mpctx;
#define MPCTX peq_mpctx
#else
#define MPCTX (*(char *volatile *)0xaf9e30u)
#endif
static int dsd(void) {
    char *mp = MPCTX, *sh = mp ? *(char **)(mp + 0x2c) : 0, *codec = sh ? *(char **)(sh + 4) : 0;
    if (!codec) return 0;
    const char *name = *(char **)(codec + 0x3d0), *drv = *(char **)(codec + 0x3e0);
    return (drv && !strcmp(drv, "dsdiff")) || (name && strlen(name) >= 5 && !memcmp(name, "ffdsd", 5));
}

static int control(af_instance *af, int command, void *arg) {
    player_state *s = af->setup;
    if (command == 0x10000100) {
        af_data *in = arg;
        if (!in || in->nch < 1 || in->nch > PEQ_CHANNELS) return -2;
        *af->data = *in;
        if (in->rate < 8000 || in->rate > 384000 || (in->format & ~63) || dsd()) {
            s->dsp.channels = 0; /* play leaves every block alone */
            return 1;
        }
        peq_load_active(&s->preset);
        peq_reset(&s->dsp, in->rate, in->nch, &s->preset);
        af->data->format = 0x1d;
        af->data->bps = 4;
        if (in->format != 0x1d || in->bps != 4) { *in = *af->data; return 0; }
        return 1;
    }
    if (command == 0x40001d00 || command == 0x40001d01) {
        struct { float *gain; int channel; } *ext = arg;
        if (!ext || !ext->gain || ext->channel < 0 || ext->channel >= PEQ_CHANNELS) return -2;
        if (command & 1) {
            memset(ext->gain, 0, 10 * sizeof(float)); /* stock's ten graphic bands */
        } else if (!ext->channel) {
            peq_preset p;
            /* Control executes on the playback loop, outside the PCM callback. */
            if (peq_load_active(&p) && memcmp(&p, &s->preset, sizeof(p)) &&
                peq_update(&s->dsp, &p)) s->preset = p;
        }
        return 1; /* Stock graphic gains never reach the replacement DSP. */
    }
    if (command == 0x20000300) return 1;
    return -1;
}

/* The visualizer's tap (peq.h): what plays, after the filter, while the visualizer shows. The file
 * appears when it first opens; until then it is looked for once a second of audio. Counting frames,
 * not time, keeps an idle tap to a compare per block. */
static void tap(const float *a, unsigned frames, int nch, int rate) {
    static vis_tap *t;
    static unsigned wait = ~0u, seen, idle;
    if (!t) {
        if (wait < (unsigned)rate) {
            wait += frames;
            return;
        }
        wait = 0;
        int fd = open(VIS_FILE, 2); /* O_RDWR */
        if (fd >= 0) {
            void *p = mmap64(0, sizeof(vis_tap), 3, 1, fd, 0); /* PROT_READ | PROT_WRITE, MAP_SHARED */
            close(fd);
            if (p != (void *)-1) t = p;
        }
    }
    if (!t) return;
    if (t->want != seen) { /* watched again after a pause: no stale audio half a second back */
        if (idle > (unsigned)rate) memset(t->ring, 0, sizeof t->ring);
        seen = t->want, idle = 0;
    }
    else if (idle > (unsigned)rate || frames > (unsigned)rate - idle) {
        idle = (unsigned)rate + 1;
        return;
    }
    else idle += frames; /* nobody watching */
    long long now = now_ns();
    unsigned step = rate > 48000 ? (unsigned)rate / 44100 : 1, seq = t->seq;
    for (unsigned i = 0; i < frames; i += step, ++seq) {
        t->ring[seq % VIS_RING][0] = a[i * nch];
        t->ring[seq % VIS_RING][1] = a[i * nch + (nch > 1)];
    }
    t->rate = (unsigned)rate / step;
    t->stamp = now;
    __asm__ volatile("" ::: "memory"); /* the frames before seq; the reader tolerates a torn one */
    t->seq = seq;
}

/* Crossfade (docs/internals.md#crossfade). Near a track's end the filter holds its last seconds back,
 * putting out half of what comes in until the hold is full, so the decoder runs ahead at twice real
 * time and no gap opens. The next track's filter (a new instance: the player re-adds it at each file)
 * then mixes the held tail under its own start, equal power. The ring outlives both instances. */
#define XF_FLOATS (1u << 20) /* ponytail: 4 MB; about 10 s at 44.1 kHz stereo, 4.7 s at 96, 2.3 s at 192 */
#define XF_SLACK (1u << 17)  /* room for one block past the held tail */
#define XF_LEAD_S 2.0        /* the hold starts this much early, for the length's whole seconds */
#define XF_STALE_NS 3000000000LL /* a tail not followed this soon (stop, pause) is dropped */
#define XF_SEEK_S 1.5        /* a position jump this large while holding is a seek */
#ifdef PEQ_HOST
extern double peq_pts;
extern int peq_length;
static double decoded(void) { return peq_pts; }
#define LENGTH peq_length
#else
/* written_audio_pts (0x44641c, a leaf): seconds decoded so far, NOPTS (-2^63) without a stream;
 * the length is {media-info}'s whole seconds (0xb10e84, demo's mclGetPlayTime). */
static double decoded(void) {
    char *mp = MPCTX, *sh = mp ? *(char **)(mp + 0x2c) : 0, *ds = mp ? *(char **)(mp + 0x34) : 0;
    return sh && ds ? ((double (*)(void *, void *))0x44641cu)(sh, ds) : -1;
}
#define LENGTH (*(volatile int *)0xb10e84u)
#endif
static struct {
    float *ring;
    unsigned head, len, total; /* frames held from head; total: held when the mix began */
    int rate, nch, owner, length; /* the holder's format, instance and length when it began */
    long long last;               /* now_ns of the holder's last block */
    double pts;                   /* its position then */
} xf;
static int xf_ids;

/* An int demo writes (XFADE_FILE, MONO_FILE), mapped read-only, looked for once a second of audio
 * until the file appears; 0 till then, or below. */
typedef struct { volatile int *f; unsigned wait; } watched;
static int watch(watched *w, const char *path, int rate, unsigned frames) {
    if (!w->f && w->wait < (unsigned)rate) {
        w->wait += frames;
    } else if (!w->f) {
        w->wait = 0;
        int fd = open(path, 0); /* O_RDONLY */
        if (fd >= 0) {
            void *p = mmap64(0, sizeof *w->f, 1, 1, fd, 0); /* PROT_READ, MAP_SHARED */
            close(fd);
            if (p != (void *)-1) w->f = p;
        }
    }
    return w->f && *w->f > 0 ? *w->f : 0;
}

/* demo's fade for the next change. */
static int xfade_ms(int rate, unsigned frames) {
    static watched w = { 0, ~0u };
    return watch(&w, XFADE_FILE, rate, frames);
}

/* n frames between a and the ring's frame at (of cap), into the ring if put: two spans where it wraps. */
static void ring_copy(float *a, unsigned at, unsigned n, unsigned cap, int nch, int put) {
    unsigned m = n < cap - at ? n : cap - at;
    float *r = xf.ring + at * nch;
    if (put) memcpy(r, a, 4 * m * nch), memcpy(xf.ring, a + m * nch, 4 * (n - m) * nch);
    else memcpy(a, r, 4 * m * nch), memcpy(a + m * nch, xf.ring, 4 * (n - m) * nch);
}

/* Frames of a (interleaved, nch) put out in place: all of them, or fewer while the hold fills. */
static unsigned crossfade(player_state *s, float *a, unsigned frames, int nch, int rate) {
    int ms = xfade_ms(rate, frames);
    if (!ms && !xf.len) return frames; /* no fade or held tail: skip clock and position queries */
    unsigned cap = XF_FLOATS / (unsigned)nch;
    long long now = now_ns();
    double pts = decoded();
    /* The next track: another instance, or this one starting over from the end (should the player
     * keep the chain across a gapless change); xf.total marks a mix under way. */
    int mine = xf.len && xf.owner == s->id,
        restart = mine && xf.pts >= xf.length - XF_SEEK_S && pts >= 0 && pts < XF_SEEK_S;
    if (xf.len && (xf.total || !mine || restart)) {
        if (now - xf.last > XF_STALE_NS || rate != xf.rate || nch != xf.nch) {
            xf.len = xf.total = 0; /* ponytail: a new rate or channel count drops the tail */
        } else {
            if (!xf.total) xf.total = xf.len;
            unsigned n = frames < xf.len ? frames : xf.len;
            /* The gains turn by a fixed angle a frame: exact at each block's start, rotated after, in
             * double (and cos d as 1 - 2 sin^2 d/2) or a long fade's rounding drifts to 1e-4. */
            float d = 1.5707963f / xf.total, p = (xf.total - xf.len + 0.5f) * d, h = sinf(d / 2);
            double in = sinf(p), out = cosf(p), sd = sinf(d), cd = 1 - 2.0 * h * h;
            float *held = xf.ring + xf.head * nch, *end = xf.ring + cap * nch;
            for (unsigned i = 0; i < n; ++i, held = held + nch == end ? xf.ring : held + nch) {
                float fi = (float)in, fo = (float)out;
                for (int c = 0; c < nch; ++c) a[i * nch + c] = a[i * nch + c] * fi + held[c] * fo;
                double t = in * cd + out * sd;
                out = out * cd - in * sd, in = t;
            }
            xf.head = (xf.head + n) % cap;
            if (!(xf.len -= n)) xf.total = 0;
            xf.last = now;
            return frames;
        }
    }
    int length = mine ? xf.length : LENGTH;
    unsigned want = (unsigned)ms * (unsigned)(rate / 10) / 100; /* 32-bit: no __divdi3 here */
    if (want > cap - XF_SLACK / (unsigned)nch) want = cap - XF_SLACK / (unsigned)nch;
    int ending = want && length > 0 && pts >= 0 && pts > length - 2.0 * want / rate - XF_LEAD_S;
    mine = xf.len && xf.owner == s->id;
    if (mine && (!ending || __builtin_fabs(pts - xf.pts) > XF_SEEK_S || xf.len + frames > cap))
        xf.len = 0; /* a seek, or no fade any more: the held audio was left behind */
    if (!ending || frames > cap - want || (!xf.ring && !(xf.ring = calloc(XF_FLOATS, sizeof(float)))))
        return frames;
    if (!xf.len) xf.head = 0, xf.owner = s->id, xf.rate = rate, xf.nch = nch, xf.length = length;
    ring_copy(a, (xf.head + xf.len) % cap, frames, cap, nch, 1);
    xf.len += frames;
    unsigned k = (frames + 1) / 2;
    if (xf.len > want && xf.len - want > k) k = xf.len - want;
    if (k > frames) k = frames;
    ring_copy(a, xf.head, k, cap, nch, 0);
    xf.head = (xf.head + k) % cap;
    xf.len -= k;
    xf.last = now;
    xf.pts = pts;
    return k;
}

static af_data *play(af_instance *af, af_data *data) {
    player_state *s = af->setup;
    if (data && data->len > 0 && data->format == 0x1d && data->bps == 4 &&
        data->nch == s->dsp.channels && data->rate == s->dsp.rate &&
        !(data->len % (4 * data->nch))) {
        unsigned frames = (unsigned)data->len / (4 * data->nch);
        peq_process(&s->dsp, data->audio, frames);
        static watched mono = { 0, ~0u }; /* Mono Audio (crossfade.c mono_sync), after the EQ's sides */
        if (data->nch == 2 && watch(&mono, MONO_FILE, data->rate, frames))
            for (float *a = data->audio, *end = a + 2 * frames; a < end; a += 2)
                a[0] = a[1] = (a[0] + a[1]) * 0.5f;
        static watched swap = { 0, ~0u };
        if (data->nch == 2 && watch(&swap, SWAP_FILE, data->rate, frames))
            for (float *a = data->audio, *end = a + 2 * frames; a < end; a += 2) {
                float left = a[0]; a[0] = a[1]; a[1] = left;
            }
        frames = crossfade(s, data->audio, frames, data->nch, data->rate);
        data->len = (int)(frames * 4 * data->nch);
        tap(data->audio, frames, data->nch, data->rate);
    }
    return data;
}

static void uninit(af_instance *af) {
    free(af->data);
    free(af->setup);
    af->data = 0;
    af->setup = 0;
}

int peq_open(af_instance *af) {
    af->control = control;
    af->uninit = uninit;
    af->play = play;
    af->mul = 1;
    af->delay = 0;
    af->data = calloc(1, sizeof(af_data));
    af->setup = calloc(1, sizeof(player_state));
    if (!af->data || !af->setup) return -2; /* af_create calls uninit on failure. */
    ((player_state *)af->setup)->id = ++xf_ids;
    return 1;
}

/* Exact VBR MP3 seeking (docs/internals.md#large-mp3s). A Xing header in the first frame h (n bytes)
 * gives the track length and a 100-point table of how far into the file each 1% of it starts. For
 * second t, *frac gets that place as a fraction of the file after the frame, read off the table
 * and interpolated between its points (t within 0..length); 0 without a VBR header that has both. */
int mp3_toc(const unsigned char *h, unsigned n, double t, double *frac, double *length) {
    static const int rates[3] = { 44100, 48000, 32000 };
    if (n < 4 || !__builtin_isfinite(t)) return 0;
    unsigned head = (unsigned)h[0] << 24 | h[1] << 16 | h[2] << 8 | h[3];
    unsigned version = head >> 19 & 3, rate = head >> 10 & 3, mono = (head >> 6 & 3) == 3;
    if (head >> 21 != 0x7ff || version == 1 || (head >> 17 & 3) != 1 || rate == 3) return 0;
    unsigned at = 4 + (!(head & 0x10000) ? 2 : 0) + (version == 3 ? (mono ? 17 : 32) : (mono ? 9 : 17));
    if (n < at + 8 + 4 + 4 + 100 || memcmp(h + at, "Xing", 4)) return 0; /* "Info": CBR, already exact */
    const unsigned char *p = h + at + 4;
    unsigned flags = p[3];
    if ((flags & 5) != 5) return 0;
    p += 4;
    unsigned frames = (unsigned)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3];
    p += 4 + (flags & 2 ? 4 : 0);
    if (!frames) return 0;
    *length = frames * (version == 3 ? 1152.0 : 576.0) / (rates[rate] >> (version == 3 ? 0 : version == 2 ? 1 : 2));
    if (t < 0) t = 0;
    if (t > *length) t = *length;
    double pct = t * 100 / *length;
    int i = pct >= 100 ? 99 : (int)pct;
    double a = p[i], b = i < 99 ? p[i + 1] : 256;
    *frac = (a + (b - a) * (pct - i)) / 256;
    return 1;
}

/* An ID3v2 TXXX frame's body f (n bytes) as "description\0value" in out (cap bytes, ASCII; other
 * characters become '?'). Latin-1, UTF-8 and UTF-16 with or without a BOM. Returns the characters
 * written, the description's NUL included. */
unsigned id3_txxx(const unsigned char *f, unsigned n, char *out, unsigned cap) {
    if (!n || f[0] > 3 || !cap) return 0;
    unsigned w = f[0] == 1 || f[0] == 2 ? 2 : 1, be = f[0] == 2, k = 0;
    for (unsigned i = 1; i + w <= n && k + 1 < cap; i += w) {
        unsigned c = w == 1 ? f[i] : be ? (unsigned)f[i] << 8 | f[i + 1] : (unsigned)f[i + 1] << 8 | f[i];
        if (c == 0xfeff) continue;
        if (c == 0xfffe) { be = !be; continue; } /* a BOM the other way round */
        out[k++] = c < 0x80 ? (char)c : '?';
    }
    out[k] = 0;
    return k;
}

#ifndef PEQ_HOST
/* MP3 ReplayGain (docs/internals.md#mp3-replaygain). demux_audio_open hands the stream, just past a
 * leading ID3v2 header, to the vendor's frame walker (0x48e6b0), which stops after 2 KB of frames, reads
 * ID3v2.4's syncsafe sizes as plain ones and keeps a UTF-8 frame's encoding byte in its description,
 * so cover art, a long frame or a UTF-8 TXXX (most 2.4 taggers) hides the gains. This
 * runs it, then walks the whole tag again through the stream's fd, as mp3_seek reads, puts each TXXX
 * into a dictionary and gives it to stock's ff_replaygain_export, as the walker does. That stores
 * the gains only when it finds one, for the replaygain filter's next setup. */
#define STOCK_ID3 ((void (*)(void *, int))0x48e6b0u)
#define AV_DICT_SET ((int (*)(void **, const char *, const char *, int))0x6ea744u)
#define AV_DICT_FREE ((void (*)(void **))0x6eabfcu)
#define RG_EXPORT ((int (*)(void *, void *))0x538764u)
#define AT(p, o, type) (*(type *)((char *)(p) + (o)))
static unsigned id3_int(const unsigned char *p, int syncsafe) {
    return syncsafe ? (p[0] & 127u) << 21 | (p[1] & 127u) << 14 | (p[2] & 127u) << 7 | (p[3] & 127u)
                    : (unsigned)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3];
}
void id3_gain(void *s, int size) {
    long long at = AT(s, 0x30, long long) + AT(s, 0x28, int) - AT(s, 0x2c, int) - 10; /* stream_tell */
    STOCK_ID3(s, size);
    int fd = AT(s, 0x14, int);
    long long back = lseek64(fd, 0, 1);
    unsigned char h[10], f[256];
    char t[256];
    void *dict = 0;
    if (back >= 0 && lseek64(fd, at, 0) == at && read(fd, h, 10) == 10 && !memcmp(h, "ID3", 3) &&
        (h[3] == 3 || h[3] == 4)) {
        int v4 = h[3] == 4;
        long long end = at + 10 + id3_int(h + 6, 1);
        at += 10;
        if (h[5] & 0x40 && read(fd, h, 4) == 4) at += v4 ? id3_int(h, 1) : 4 + id3_int(h, 0); /* extended header */
        while (at + 10 <= end && lseek64(fd, at, 0) == at && read(fd, h, 10) == 10 && h[0]) {
            unsigned n = id3_int(h + 4, v4), k, key;
            if (!memcmp(h, "TXXX", 4) && n <= sizeof f && read(fd, f, n) == (int)n &&
                (k = id3_txxx(f, n, t, sizeof t)) > (key = strlen(t)) + 1)
                AV_DICT_SET(&dict, t, t + key + 1, 0);
            at += 10 + n;
        }
        RG_EXPORT(0, dict);
        AV_DICT_FREE(&dict);
    }
    if (back >= 0) lseek64(fd, back, 0);
}

/* demux_audio_seek (0x48d1ac, the audio demuxer's seek slot). For an MP3 (priv->frmt 1, priv at
 * demuxer+0xc70) without hr_mp3_seek, stock lands at movi_start + seconds * average bytes per
 * second and sets priv->next_pts (a double at +8) from that place. With a Xing table this seeks to
 * the table's place for the second instead, as a fraction of movi_start..movi_end (stock's
 * SEEK_ABSOLUTE | SEEK_FACTOR), then sets next_pts to the second itself. Each seek reads the
 * first frame through the stream's fd (stream_t +0x14) and puts its offset back, since stock's
 * stream_seek may reuse its buffer without seeking. STOCK_SEEK is SEEK_SLOT's pinned stock value
 * in tools/peq.py. */
#define STOCK_SEEK ((void (*)(void *, float, float, int))0x48d1acu)
#define HR_MP3_SEEK (*(volatile int *)0xb152b8u)
void mp3_gapless_seek(void *demuxer, int at_start);
void mp3_seek(void *demuxer, float rel, float delay, int flags) {
    unsigned char h[192];
    void *priv = AT(demuxer, 0xc70, void *), *s = AT(demuxer, 0x20, void *);
    long long movi_start = AT(demuxer, 0x10, long long), movi_end = AT(demuxer, 0x18, long long);
    double frac, length;
    if (priv && AT(priv, 0, int) == 1 && !HR_MP3_SEEK && !(flags & 2) && s && movi_end > movi_start) {
        int fd = AT(s, 0x14, int), got = 0;
        long long back = lseek64(fd, 0, 1);
        if (back >= 0) {
            if (lseek64(fd, movi_start, 0) == movi_start) got = read(fd, h, sizeof h);
            lseek64(fd, back, 0);
        }
        double t = flags & 1 ? rel : AT(priv, 8, double) + rel;
        if (t < 0) t = 0;
        if (got > 0 && mp3_toc(h, (unsigned)got, t, &frac, &length)) {
            if (t > length) t = length;
            STOCK_SEEK(demuxer, (float)frac, delay, 3);
            AT(priv, 8, double) = t;
            mp3_gapless_seek(demuxer, t <= 0);
            return;
        }
    }
    STOCK_SEEK(demuxer, rel, delay, flags);
    if (priv && AT(priv, 0, int) == 1) mp3_gapless_seek(demuxer, AT(priv, 8, double) <= 0);
}
#endif
