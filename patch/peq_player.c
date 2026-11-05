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

/* Filter memory across a track change (#137): the player negotiates a new instance (or this one
 * again) for each file, and a cascade starting from zero memory under a continuous signal clicks at
 * a gapless change. The instance that played last leaves its settled filter here when it goes or
 * renegotiates, if it was filtered to within XF_LEAD_S of its length (crossfade's position and
 * length); the next one adopts it on its first block at the same rate, channels and preset. A skip
 * or stop mid-track, or a pause, starts the next from zero.
 * ponytail: a new instance that plays before the old one is freed starts from zero, as before; a
 * skip in a track's last two seconds carries. */
#define CARRY_STALE_NS 3000000000LL /* as XF_STALE_NS: older (a pause) would ring into the next */
static struct {
    double z[PEQ_CHANNELS][PEQ_BANDS][2];
    peq_preset p;
    int rate, channels, id;
} carry;
static int last_id;       /* the instance that played the last block, filtered or passed through */
static long long last_at; /* now_ns of the last block filtered near its track's end */
static void leave(player_state *s) {
    if (s->id != last_id || s->fresh) return;
    carry.id = 0;
    if (!s->ending || s->dsp.ramp || s->dsp.waiting || s->dsp.current.bypass || !s->dsp.channels)
        return;
    memcpy(carry.z, s->dsp.current.z, sizeof carry.z), carry.p = s->preset;
    carry.rate = s->dsp.rate, carry.channels = s->dsp.channels, carry.id = s->id;
}
static void adopt(player_state *s) {
    int id = carry.id;
    s->fresh = 0, carry.id = 0; /* one track takes it */
    if (id && id == last_id && carry.rate == s->dsp.rate && carry.channels == s->dsp.channels &&
        !memcmp(&carry.p, &s->preset, sizeof carry.p) && now_ns() - last_at < CARRY_STALE_NS)
        memcpy(s->dsp.current.z, carry.z, sizeof carry.z);
}

static int control(af_instance *af, int command, void *arg) {
    player_state *s = af->setup;
    if (command == 0x10000100) {
        af_data *in = arg;
        if (!in || in->nch < 1 || in->nch > PEQ_CHANNELS) return -2;
        leave(s);
        *af->data = *in;
        if (in->rate < 8000 || in->rate > 384000 || (in->format & ~63) || dsd()) {
            s->dsp.channels = 0; /* play leaves every block alone */
            return 1;
        }
        peq_load_active(&s->preset);
        peq_reset(&s->dsp, in->rate, in->nch, &s->preset);
        s->fresh = 1;
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
 * then mixes the held tail under its own start, equal power, at its own rate. The ring outlives
 * both instances. */
#define XF_FLOATS (6u << 18) /* ponytail: 6 MB; 15 s at 48 kHz stereo, 7.5 s at 96, 3.7 s at 192 */
#define XF_SLACK (1u << 17)  /* room for one block past the held tail */
#define XF_LEAD_S 2.0        /* the hold starts this much early, for the length's whole seconds */
#define XF_STALE_NS 3000000000LL /* a tail, or a mix, no other track takes on this soon (stop) is dropped */
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
    unsigned head, len;           /* frames held from head */
    unsigned total, left;         /* the mix: its frames at the next track's rate, and those still to go */
    double pos, step;             /* its read position in the held frames, and held frames per frame out */
    int out, mixer;               /* the mix's output rate, and the instance that mixed last */
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
        /* The mix begins on the first block after the hold: a tail not followed within XF_STALE_NS
         * (a stop) is dropped; another rate plays it through linear interpolation (step held frames
         * an output frame), a fading tail, so that is enough. Mid-mix, its track's pause only waits;
         * a track at another rate, or one more than XF_STALE_NS on (a stop, then another play),
         * drops what is left. ponytail: a skip mid-mix to a track at its rate fades the rest under
         * that one; another channel count drops it, mid-mix too (the ring is read with nch). */
        if (xf.total && (rate != xf.out || (s->id != xf.mixer && now - xf.last > XF_STALE_NS)))
            xf.len = xf.total = 0;
        else if (!xf.total) {
            if (now - xf.last <= XF_STALE_NS) {
                xf.step = (double)xf.rate / rate, xf.pos = 0, xf.out = rate;
                xf.total = xf.left = (unsigned)(xf.len / xf.step);
            }
            if (!xf.total) xf.len = 0;
        }
        if (nch != xf.nch) xf.len = xf.total = 0;
        if (xf.total) {
            unsigned n = frames < xf.left ? frames : xf.left;
            /* The gains turn by a fixed angle a frame: exact at each block's start, rotated after, in
             * double (and cos d as 1 - 2 sin^2 d/2) or a long fade's rounding drifts to 1e-4. */
            float d = 1.5707963f / xf.total, p = (xf.total - xf.left + 0.5f) * d, h = sinf(d / 2);
            double in = sinf(p), out = cosf(p), sd = sinf(d), cd = 1 - 2.0 * h * h;
            for (unsigned i = 0; i < n; ++i, xf.pos += xf.step) {
                unsigned at = (unsigned)xf.pos, next = at + 1 < xf.len ? at + 1 : at;
                float frac = (float)(xf.pos - at), fi = (float)in, fo = (float)out;
                if ((at += xf.head) >= cap) at -= cap;
                if ((next += xf.head) >= cap) next -= cap;
                const float *h0 = xf.ring + at * nch, *h1 = xf.ring + next * nch;
                for (int c = 0; c < nch; ++c)
                    a[i * nch + c] = a[i * nch + c] * fi + (h0[c] + (h1[c] - h0[c]) * frac) * fo;
                double t = in * cd + out * sd;
                out = out * cd - in * sd, in = t;
            }
            if (!(xf.left -= n)) xf.len = xf.total = 0;
            xf.last = now, xf.mixer = s->id;
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
        if (s->fresh) adopt(s);
        if (!s->dsp.current.bypass) { /* only a track played to its end hands its memory on */
            double pts = decoded();
            s->ending = LENGTH > 0 && pts >= 0 && pts > LENGTH - XF_LEAD_S;
            if (s->ending) last_at = now_ns();
        }
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
    if (data && data->len > 0) last_id = s->id;
    return data;
}

static void uninit(af_instance *af) {
    if (af->setup) leave(af->setup);
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

#ifndef PEQ_HOST
/* Bluetooth's resampler across a gapless change (docs/internals.md#bluetooth-resampler). ALSA runs at
 * 48 kHz there, so for any other rate the player appends lavcresample (af_info at 0x8a4540), a new
 * instance each file. Its SwrContext holds the last half filter of input back until more comes:
 * freed with the instance (0x4ac534), that never plays, and the next file arrives about 10 frames
 * early, a click. An instance freed after a block within XF_LEAD_S of its track's end (read here,
 * as the PEQ filter is not in the chain with the EQ off from boot) keeps its context here; the next
 * one swaps it for its own fresh one on its first block, if both were set up alike (ctx_*, what
 * stock's reinit compares, and channels) and under CARRY_STALE_NS later.
 * ponytail: as carry, a skip in a track's last two seconds carries too. */
typedef struct {
    void *swr, *in, *tmp;
    int alloc[2], filter[3];
    double cutoff;
    char ctx[32]; /* ctx_out_rate, ctx_in_rate, filter size, phase shift, linear (+pad), ctx_cutoff */
} lavc_resample;
_Static_assert(sizeof(lavc_resample) == 0x48 && __builtin_offsetof(lavc_resample, ctx) == 0x28, "lavcresample ABI");
#define SWR_FREE ((void (*)(void **))0x41e2d0u)
static struct { void *swr; char ctx[32]; int nch; long long at; } rs;
static lavc_resample *rs_last; /* the instance that resampled the last block */
static long long rs_end;       /* now_ns of that block if near its track's end, else 0 */
static af_data *rs_play(af_instance *af, af_data *data) {
    lavc_resample *s = af->setup;
    if (s != rs_last) {
        void *swr = rs.swr;
        rs.swr = 0, rs_last = s;
        if (swr && rs.nch == af->data->nch && !memcmp(rs.ctx, s->ctx, sizeof rs.ctx) &&
            now_ns() - rs.at < CARRY_STALE_NS) {
            void *fresh = s->swr;
            s->swr = swr, swr = fresh;
        }
        SWR_FREE(&swr);
    }
    double pts = decoded();
    rs_end = LENGTH > 0 && pts >= 0 && pts > LENGTH - XF_LEAD_S ? now_ns() : 0;
    return ((af_data * (*)(af_instance *, af_data *))0x4ac35cu)(af, data);
}
static void rs_uninit(af_instance *af) {
    lavc_resample *s = af->setup;
    if (s && s == rs_last) {
        rs_last = 0;
        if (rs_end && now_ns() - rs_end < CARRY_STALE_NS) {
            SWR_FREE(&rs.swr);
            rs.swr = s->swr, s->swr = 0, rs.nch = af->data->nch, rs.at = now_ns();
            memcpy(rs.ctx, s->ctx, sizeof rs.ctx);
        }
    }
    ((void (*)(af_instance *))0x4ac534u)(af);
}
int rs_open(af_instance *af) {
    int r = ((int (*)(af_instance *))0x4ac2c0u)(af);
    af->play = rs_play, af->uninit = rs_uninit;
    return r;
}

/* Bluetooth and a USB DAC across a gapless change (docs/internals.md#usb-output-buffer). ao_alsa's init
 * (0x438988, the descriptor's slot 0x88f760) keeps the PCM open when the file's rate, channels and format equal
 * what the last call asked, but it saves the negotiated rate and format as that (0x4393b8, 0x43934c). The player
 * asks Bluetooth for 48 kHz whatever the file (0x42efcc) and bluealsa's ioplug offers the transport's one rate
 * (LDAC 96 kHz; 48 kHz for AAC and SBC, so those matched), and it runs a USB DAC whose top rate is 48 kHz at
 * 48 kHz for any file (0x43999c), so at any other answer every change drained or lost the buffer, closed and
 * reopened the PCM. The same request asks for what it got last time instead: the compare matches and ao_data
 * keeps the negotiated values, so the chain resamples as before. The pair moves as one, on a fresh open (another
 * DAC or transport may have come), when the request was changed, or when the request it holds was taken as asked
 * (a reopen found a new answer); another request taken as asked leaves it, so a 44.1 and 48 kHz mix stays
 * matched. Another request, a closed PCM (a stop; stock's own init calls at 0x439b5c, the hw_params retry, and
 * 0x439d14, reset's, bypass the slot) or the headphone jack negotiate as stock, and a pair that changes nothing
 * (AAC, SBC, a DAC that takes the file's format) isn't even looked at. A transport gone under an open PCM
 * (reconnect, codec change) leaves the ioplug disconnected or, when it had underrun first, in XRUN with its dead
 * FIFO, and stock's play and reset only prepare it, so only a PCM still running or prepared is kept. The ioplug's
 * state op only reads (libasound 0x904d0): the plugin's pointer callback moves it, and that runs from
 * snd_pcm_status, hence stock's get_space (0x43815c) first. Every other state, and another output way (the pair is
 * that way's), negotiates as stock. ponytail: one request remembered, so a queue that changes bit depth or channels
 * reopens at each change, as AAC does; a next track later than the buffer underruns, so it reopens, as stock did at
 * every change; on a USB DAC or the jack a dry PCM is caught below: at init it reopens, and by the first write ao_write
 * moves past it; a server that closed the FIFO under a PCM with nothing yet underrun stays the hole stock AAC has:
 * the first write finds it (docs/internals.md#usb-output-buffer). */
#define OUTPUT_WAY (*(volatile int *)0xb10e54u)
#define ALSA_PCM (*(void *volatile *)0xb115a8u)
#define AO_RATE (*(volatile int *)0xafa1a0u)
#define AO_FORMAT (*(volatile int *)0xafa1a8u)
#define PCM_PREPARED 2 /* SND_PCM_STATE_*, from snd_pcm_state (0xadb680) */
#define PCM_RUNNING 3
static int ao_asked[2], ao_got[2], ao_way; /* the last request the output changed, what it got, and on which way */
static int ao_playing(void) {
    ((int (*)(void))0x43815cu)(); /* stock's get_space: snd_pcm_status brings the ioplug's state up to date */
    int state = ((int (*)(void *))0xadb680u)(ALSA_PCM);
    return state == PCM_RUNNING || state == PCM_PREPARED;
}
/* The old track's end at a natural change that reopens the PCM (docs/internals.md#usb-output-buffer). Init's reopen
 * mutes the DAC (0x4395dc) before it drains (0x438d04), and on USB closes without draining (0x438cfc), so up to the
 * buffer (500 ms at 44.1 kHz, 85 ms at 192) of a track played to its end went unheard. So a change nobody pressed for drains first, then
 * stock's mute, drain (bad state, returning at once in libasound, its answer unread), close and open run.
 * What says nobody pressed: every file's end reaches the uninit slot (0x88f764), whose immed is 0 only when the
 * loop ended it naturally (eof 1); a skip (set-nexturl, the next flag 0xb10e6c) and a stop (mcl-stop, the stop flag
 * 0xb10e70) make eof 4, immed 1. A next flag up then, or a next or stop flag set by a command before the drain
 * (hci_command, and init's own look), means a press and stock's mute-first order. A stop flag up at the slot closes
 * the PCM there, so init finds nothing to drain. The arm is that way's, so it can't drain across outputs.
 * A press during the drain, which stock's handler only notes, must silence: hci_command mutes the jack (0x43b280:
 * its flag is what stock's unmute reads) and drops USB, whose mute wouldn't reach the DAC. snd_pcm_drain holds no
 * lock (libasound 0x3ff44, hw 0x4f784) and plug forwards it, and a kernel DROP ends a blocked DRAIN. A rise of the
 * play flag is a press here too: set-songurl (demo's not-playing path, whose mute a hardware-type word and its
 * output way decide, 0x5ad1bc) sets
 * no other word, and the flag was cleared on the way to this init (0x42d9fc). ao_draining keeps the PCM open
 * while the other thread uses it: 1 draining, 2 being cut. The triple can't say: a cold natural change (Gapless off, stock Shuffle,
 * Repeat One, a track under 3 s) resets it just as a skip does. Same-format gapless changes match stock's compare,
 * so nothing is called. DSD (0xb10e5c, at either end) keeps its mute, which may guard an end pop. Bluetooth drains
 * unmuted in stock's own code. A stop flag with immed 0 (a stop within one loop of the natural end) stays stock's.
 * The drain runs on the player's main thread, as stock's muted one did, for the same time; on USB it adds what
 * was lost, the buffer's rest.
 * ponytail: a press from demo's not-playing state before the drain starts (within a UI pass of the end) can't be
 * told from the cold route's own set-songurl and hears the tail play out, up to the buffer's rest; demo's press word
 * would close that. Demo's mute on the cold route (0x5ad1bc, 100 ms before set-songurl) is skipped by start_mute for a
 * natural start from a local queue, and marks PRESS_FILE even: then a same format with Gapless on matches stock's
 * compare, so the PCM never closes and nothing mutes, and any real reopen is drain, stock's mute, close, open, unmute,
 * wait, frames (dac_ioctl). No mark means demo may have muted: stock's timing, no wait.
 * mcl-orderstop's close on the command thread during the drain races it as it races stock's own
 * drain. ponytail: a USB stream hung during this drain runs to the kernel's 10 s, past the heartbeat's 6 ticks
 * (0x42d95c), and hciplayer exits; stock's 0x438768 has the same exposure. A close always stays under stock's mute, and the PCM only stops unmuted once drained. */
#define NEXT_FLAG (*(volatile int *)0xb10e6cu)
#define STOP_FLAG (*(volatile int *)0xb10e70u)
#define DSD_FILE (*(volatile int *)0xb10e5cu)
#define KEEP_GAPLESS (*(volatile int *)0xb10e58u)
#define PREV_FORMAT (*(volatile int *)0xafa164u) /* stock's triple, compared at 0x438cc0, 0x439598, 0x43972c */
#define PREV_RATE (*(volatile int *)0xafa168u)
#define PREV_CHANNELS (*(volatile int *)0xafa16cu)
#define AO_CHANNELS (*(volatile int *)0xafa1a4u)
#define AO_OUTBURST (*(volatile unsigned *)0xafa1b0u)
#define AO_BUFFERSIZE (*(volatile unsigned *)0xafa1b4u) /* the ring, in bytes */
#define AO_BYTES (*(volatile unsigned *)0xb11598u)
static volatile int ao_natural, ao_draining; /* ao_natural: the way a natural end was on, plus 1 */
static int ao_wait;   /* the reopen under way is a natural one: dac_ioctl waits for the line */
static int ao_stayed; /* a natural change kept the PCM open: ao_write looks at the first write */
static int ao_pcm[3]; /* what the open PCM is: reinit_audio_chain overwrites ao_data with the request before init */
static int dac_word(int pressed);
/* Frames free, hw_ptr past appl_ptr making it more than the ring. Not stock's get_space, which caps MQA's below it. */
static unsigned ao_avail(void *pcm) {
    char status[256] __attribute__((aligned(8)));
    ((int (*)(void *, void *))0xadabd0u)(pcm, status);
    return ((unsigned (*)(void *))0xadb7b0u)(status);
}
void ao_uninit(int immed) {
    ao_natural = !immed && !NEXT_FLAG && !DSD_FILE ? OUTPUT_WAY + 1 : 0;
    if (ao_natural) ao_pcm[0] = AO_RATE, ao_pcm[1] = AO_CHANNELS, ao_pcm[2] = AO_FORMAT;
    ((void (*)(int))0x43868cu)(immed);
}
int ao_init(int rate, int channels, int format, int flags) {
    int asked[2] = { rate, format }, was_open = ALSA_PCM != 0;
    int held = OUTPUT_WAY == ao_way && rate == ao_asked[0] && format == ao_asked[1];
    if (OUTPUT_WAY && was_open && held && (rate != ao_got[0] || format != ao_got[1]) && ao_playing())
        rate = ao_got[0], format = ao_got[1];
    int natural = ao_natural;
    ao_natural = 0;
    ao_stayed = 0;
    if (natural == OUTPUT_WAY + 1 && was_open && OUTPUT_WAY != 1 && !DSD_FILE) {
        int preloaded = PREV_RATE != -1;
        int told = !OUTPUT_WAY && !preloaded && dac_word(0); /* demo left its mute off for this start (start_mute) */
        if (told && channels == ao_pcm[1] && rate == ao_pcm[0] && format == ao_pcm[2] && ao_playing())
            PREV_CHANNELS = channels, PREV_RATE = rate, PREV_FORMAT = format; /* nothing to reopen for */
        int reopens = !KEEP_GAPLESS || channels != PREV_CHANNELS || rate != PREV_RATE || format != PREV_FORMAT;
        /* Ran dry: stop_threshold and silence_size are the boundary (0x4394dc, 0x4394f0, every way), so the PCM
         * still runs, on silence, with hw_ptr past appl_ptr, and the new track's first frames would land behind it. */
        if (!reopens && ao_avail(ALSA_PCM) >= AO_BUFFERSIZE / AO_BYTES) reopens = 1, PREV_RATE = -1;
        ao_stayed = !reopens;
        if (reopens) {
            int was;
            __atomic_store_n(&ao_draining, 1, __ATOMIC_SEQ_CST); /* before the flags: hci_command sets them, then looks */
            if (!NEXT_FLAG && !STOP_FLAG) ((int (*)(void *))0xadb6a0u)(ALSA_PCM); /* snd_pcm_drain; an error leaves it to stock's own */
            while ((was = __sync_val_compare_and_swap(&ao_draining, 1, 0)) == 2) usleep(1000);
            ao_wait = was == 1 && !NEXT_FLAG && !STOP_FLAG && (told || preloaded);
        }
    }
    int r = ((int (*)(int, int, int, int))0x438988u)(rate, channels, format, flags);
    ao_wait = 0;
    if (r > 0 && ALSA_PCM) {
        int got[2] = { AO_RATE, AO_FORMAT };
        if (!was_open || held || OUTPUT_WAY != ao_way || asked[0] != got[0] || asked[1] != got[1])
            ao_asked[0] = asked[0], ao_asked[1] = asked[1], ao_got[0] = got[0], ao_got[1] = got[1], ao_way = OUTPUT_WAY;
    }
    return r;
}
static void ao_cut(void) { /* a press during ao_init's drain, on the command thread */
    if (__sync_val_compare_and_swap(&ao_draining, 1, 2) != 1) return;
    void *pcm = ALSA_PCM; /* mcl-orderstop's close may have run */
    if (OUTPUT_WAY != 2) ((void (*)(int))0x43b280u)(1);
    else if (pcm) ((int (*)(void *))0xadaf60u)(pcm); /* snd_pcm_drop */
    __atomic_store_n(&ao_draining, 0, __ATOMIC_SEQ_CST);
}

/* The wired outputs' cut start (docs/internals.md#wired-start). ioctl DAC_MUTE 0 returns with the driver's
 * mute line still held for 90-100 ms, and after a pause or seek the volume is still ramping, so what played
 * at once was cut, heard as a swift fade-up. Each place stock releases on output way 0 with the DAC open now
 * releases, waits, then lets audio flow: the PCM is prepared or paused, so nothing runs out and the clock
 * stands still, and no frame is written, so DSD stays bit-exact. Stock unmutes once per mute: the flag
 * (0xb11584, 1 after hciplayer's mute) gates every site and each clears it, so one action waits once.
 * Init and seek wait only when demo's PRESS_FILE holds a fresh stamp, so a track change nobody pressed for
 * has no sleep, Crossfade's and Gapless's above all; a resume always waits, only ever a press or a phone's.
 * ponytail: a wired manual start comes about 130 ms later. A stamp left unspent under 5 s (a refused Next, a
 * press that opened paused, a seek while paused, a press and a track under 5 s) gives a natural reopen one
 * 130 ms gap, at most one per word; a per-file stamp would close that. A rapid double Next can spend its word
 * on the first init, so the last track keeps the old cut. A-B Repeat, Resume Songs' jump, a phone's seek,
 * Spotify's aplay and q2video keep the cut. Whether a start pops with the gate open can only be settled by ear. */
#define DAC_RELEASE_MS 130 /* derived, not measured: the mute line's 90-100 ms and the ramp at top volume */
#define DAC_PRESS_MS 5000  /* demo's mute, its sleep and a slow card's open, from the press to init's end */
#define DAC_FD (*(volatile int *)0xb1158cu)
#define DAC_MUTED (*(volatile int *)0xb11584u)
#define DAC_HELPER ((void (*)(int))0x43b280u) /* stock's: checks the way and the fd, clears the flag */
/* Whether PRESS_FILE holds a fresh stamp, each used once, so a short track's natural change after the press's
 * own start doesn't wait. Stale, from the future, 0 or no file: no. Read only at these sites, never per block. */
static int dac_word(int pressed) { /* odd: a press's stamp; even: start_mute's mark */
    static int seen;
    int at = 0, fd = open(PRESS_FILE, 0); /* O_RDONLY */
    if (fd < 0) return 0;
    read(fd, &at, sizeof at);
    close(fd);
    if (!at || (at & 1) != pressed || at == seen || (unsigned)now_ms() - (unsigned)at >= DAC_PRESS_MS) return 0;
    seen = at;
    return 1;
}
static int dac_told(void) { return dac_word(1); }
/* The end of init (0x4391b0), past stock's checks of the way and the fd: the ioctl is stock's own. */
int dac_ioctl(int fd, unsigned request, void *arg) {
    int r = ioctl(fd, request, arg);
    if (dac_told() || ao_wait) usleep(DAC_RELEASE_MS * 1000);
    return r;
}
/* seek (0x42b164), a start offset's or an mcl-seek's: demo stamps a user's, not A-B Repeat's loop. */
void dac_unmute(int value) {
    int wait = !OUTPUT_WAY && DAC_FD > 0 && dac_told();
    DAC_HELPER(value);
    if (wait) usleep(DAC_RELEASE_MS * 1000);
}
/* An unpause on the headphone DAC (docs/internals.md#unpause). soc_aic.ko restarts the DMA at the ring's base on
 * PAUSE_RELEASE and the pointer jumps to the next wrap, so what the ring held past the pause never played; and
 * demo's mute(2) fades the volume and closes the line well before the pause, so the music under it went unheard.
 * The last frames written are kept here. A resume drops and prepares the PCM, still muted, then writes them again
 * from the fade's second step, by demo's stamp in MUTE_FILE against the pause's own trigger time. A prepare
 * zeroes hw_ptr, so hw_ptr bounds how far back is this stream's: a seek, a reopen or an earlier unpause can't leak
 * frames in, with no hook on any of them. Only way 0's writes are kept, one memcpy per write, and no byte a
 * natural change writes differs. */
#define PS_BYTES (1u << 21) /* touched only as far as a format's cap: 176 KB at 44.1 kHz S16, 899 KB at 192 kHz S32 */
#define PS_HOLD_MS 500      /* frames kept beyond the ring: the fade and the sleep after it, with room to spare */
#define PS_FRESH_MS 1000    /* a stamp older than this at the pause is not this pause's, so nothing goes back */
#define PS_TRIM_MS (-29)    /* the stamp to mute(2)'s second volume write: the join (docs/internals.md#unpause) */
#define PS_FADE_MS 4        /* the first frames rise from 0: the line is open, and they'd step from silence */
#define PCM_WRITEI ((long (*)(void *, const void *, unsigned long))0xadb220u)
#define PCM_PAUSE ((int (*)(void *, int))0xadacf0u)
#define PCM_DROP ((int (*)(void *))0xadaf60u)
#define PCM_PREPARE ((int (*)(void *))0xadb2b0u)
#define PCM_STATUS ((int (*)(void *, void *))0xadabd0u)
#define PCM_PAUSED 6
#define SEEK_PENDING (*(volatile int *)0xb10c38u)   /* mcl-seek's, set before the mcl-play demo sends after it */
/* snd_pcm_status_t, 0x6c bytes (snd_pcm_status_sizeof): the library's getters read these same words. */
typedef struct { int state, trigger_s, trigger_ns, tstamp[2]; unsigned appl, hw; int rest[20]; } pcm_status;
_Static_assert(sizeof(pcm_status) == 0x6c, "snd_pcm_status_t ABI");
static char ps_ring[PS_BYTES] __attribute__((aligned(8)));
static struct { unsigned at, fill, cap, frame; int rate; } ps; /* at: the next byte's place; fill: bytes held */

/* ao_alsa's play (0x43afa4), its one writei. A kept PCM can still run dry while the new track's first block decodes
 * (nearly a whole buffer, 0x42f3f4), and what it then writes lands behind hw_ptr and is lost. The ring there is silence
 * (silence_size), so appl_ptr moves up to a period past hw_ptr first: a short gap, not a cut. The kernel limits the
 * forward to avail, which is more than asked for here. hciplayer doesn't import snd_pcm_forward.
 * After stock's write, what the DAC took is kept, and a write anywhere else forgets it, so a PCM paused on Bluetooth
 * or USB when the way turns to 0 finds nothing to take for its own. (Not by the PCM's handle: a reopen's snd_pcm_t can
 * land on the old one's address.) A write is at most the ring (get_space), and the cap is never less. */
long ao_write(void *pcm, const char *data, unsigned long frames) {
    if (ao_stayed) {
        unsigned avail = ao_avail(pcm), ring = AO_BUFFERSIZE / AO_BYTES;
        ao_stayed = 0;
        if (avail > ring) {
            long (*forward)(void *, unsigned long) = dlsym(0, "snd_pcm_forward"); /* RTLD_DEFAULT */
            if (forward) forward(pcm, avail - ring + AO_OUTBURST / AO_BYTES);
        }
    }
    long r = PCM_WRITEI(pcm, data, frames);
    if (r <= 0) return r;
    if (OUTPUT_WAY) {
        ps.fill = 0;
        return r;
    }
    unsigned frame = AO_BYTES, n = (unsigned)r * frame;
    if (frame != ps.frame || AO_RATE != ps.rate) {
        ps.frame = frame, ps.rate = AO_RATE, ps.at = ps.fill = 0;
        ps.cap = AO_BUFFERSIZE + (unsigned)AO_RATE * frame / 1000 * PS_HOLD_MS;
        if (ps.cap > PS_BYTES) ps.cap = PS_BYTES;
        ps.cap -= ps.cap % frame;
    }
    unsigned m = n < ps.cap - ps.at ? n : ps.cap - ps.at;
    memcpy(ps_ring + ps.at, data, m);
    memcpy(ps_ring, data + m, n - m);
    ps.at = ps.at + n >= ps.cap ? ps.at + n - ps.cap : ps.at + n;
    ps.fill = n > ps.cap - ps.fill ? ps.cap : ps.fill + n;
    return r;
}

/* Frames back from the pause to the fade's start, within what is held and this stream played. */
static unsigned ps_back(const pcm_status *st, unsigned left) {
    int at = 0, fd = open(MUTE_FILE, 0); /* O_RDONLY */
    if (fd < 0) return 0;
    read(fd, &at, sizeof at);
    close(fd);
    unsigned age = (unsigned)st->trigger_s * 1000u + (unsigned)st->trigger_ns / 1000000u - (unsigned)at;
    int ms = (int)age + PS_TRIM_MS;
    if (age >= PS_FRESH_MS || ms <= 0) return 0; /* no stamp reads as 0, an age of the whole uptime */
    unsigned back = (unsigned)ms * (unsigned)ps.rate / 1000, room = ps.fill / ps.frame - left;
    if (back > st->hw) back = st->hw;
    return back < room ? back : room;
}

/* One sample times gain in place, by MPlayer's af_format bits: 1 little-endian, 2 unsigned, 4 float; bits 3-4 its
 * bytes less one. */
static void ps_scale(unsigned char *p, unsigned w, int format, double gain) {
    unsigned v = 0;
    for (unsigned i = 0; i < w; ++i) v |= (unsigned)p[format & 1 ? i : w - 1 - i] << (i * 8);
    if (format & 4) {
        float f;
        memcpy(&f, &v, 4);
        f = (float)(f * gain);
        memcpy(&v, &f, 4);
    } else {
        unsigned shift = 32 - w * 8;
        v <<= shift;
        if (format & 2) v ^= 0x80000000u;
        v = (unsigned)(int)((int)v * gain);
        if (format & 2) v ^= 0x80000000u;
        v >>= shift;
    }
    for (unsigned i = 0; i < w; ++i) p[format & 1 ? i : w - 1 - i] = (unsigned char)(v >> (i * 8));
}

/* The held frames from n before the newest to the newest, into a prepared PCM: its start threshold starts it, and
 * what doesn't fit the ring waits for room, at most the back's length, on a thread that has nothing else to do.
 * The first PS_FADE_MS rise from silence, whatever the format; only DSD is left bit-exact. In place: a second pause
 * could only reach them again by going back as far, and then they fade again. */
static void ps_write(void *pcm, unsigned n) {
    unsigned left = n * ps.frame, at = ps.at >= left ? ps.at - left : ps.at + ps.cap - left;
    if (!dsd()) {
        unsigned fade = (unsigned)ps.rate / 1000 * PS_FADE_MS, w = (AO_FORMAT >> 3 & 3) + 1;
        if (fade > n) fade = n;
        for (unsigned i = 0, p = at; i < fade; ++i, p = p + ps.frame >= ps.cap ? p + ps.frame - ps.cap : p + ps.frame)
            for (unsigned b = 0; b + w <= ps.frame; b += w)
                ps_scale((unsigned char *)ps_ring + p + b, w, AO_FORMAT, (double)i / fade);
    }
    while (left) {
        unsigned m = left < ps.cap - at ? left : ps.cap - at;
        long r = PCM_WRITEI(pcm, ps_ring + at, m / ps.frame);
        if (r == -4 || !r) continue; /* EINTR, as stock's play loop */
        if (r < 0) return;           /* the next play's write recovers as stock's does */
        at = at + (unsigned)r * ps.frame >= ps.cap ? 0 : at + (unsigned)r * ps.frame;
        left -= (unsigned)r * ps.frame;
    }
}

/* Resume (0x43881c) calls snd_pcm_pause(pcm, 0) before its unmute (0x438978): that one goes first now, and with the
 * flag clear, stock's check after the call (0x438844) passes it over. On way 0 the PCM is dropped and prepared
 * under the mute, the release waited for, and only then is anything written; with a seek pending nothing is, as
 * its reset comes next and no old frame should sound. Anything but a paused PCM whose ring is all held (a pause
 * that failed, Bluetooth, USB; hw_ptr past appl_ptr reads as more than is held) is stock's. */
int dac_resume(void *pcm) {
    pcm_status st;
    int redo = 0;
    unsigned n = 0;
    if (!OUTPUT_WAY && pcm && PCM_STATUS(pcm, &st) >= 0 && st.state == PCM_PAUSED) {
        unsigned left = st.appl - st.hw;
        if (SEEK_PENDING) redo = 1;
        else if (ps.frame == AO_BYTES && ps.rate == AO_RATE && left <= ps.fill / ps.frame) {
            n = left + ps_back(&st, left);
            redo = 1;
        }
    }
    if (redo) PCM_DROP(pcm), PCM_PREPARE(pcm);
    if (DAC_MUTED == 1) {
        DAC_HELPER(0);
        if (!OUTPUT_WAY && DAC_FD > 0) usleep(DAC_RELEASE_MS * 1000);
    }
    if (!redo) return PCM_PAUSE(pcm, 0);
    if (n) ps_write(pcm, n);
    return 0;
}
#endif

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

/* The eof stock forces once the whole-second position passes the header's length by more than a second (0x4232bc).
 * Only an MP3's Xing or Info frame count is trusted: past it, libmad finding false syncs in whatever follows the
 * audio is noise, up to full scale, about 0.6 s per MB of junk. Every other length (WMA's preroll, a VBR MP3 with no
 * header, a FLAC's understated STREAMINFO) is only an estimate and cut real audio. Whole seconds and no doubles: the
 * main loop's registers are live here (forced_eof saves them), and it's only past the length, so one read per pass
 * of a track that's over. docs/internals.md#forced-eof.
 * ponytail: the real ceiling is noise. A no-header MP3 followed by junk still plays it, to full scale, as stock: only
 * a header says where the audio ends. Elapsed runs ahead of the total meanwhile. A count over 27 hours isn't trusted. */
#define AUDIO_DEMUXER 0x89fd90u
int forced_eof_c(char *mp, int pos) {
    static const unsigned short rates[3] = { 44100, 48000, 32000 };
    unsigned char h[64];
    void *d = AT(mp, 0x28, void *), *priv = d ? AT(d, 0xc70, void *) : 0, *s = d ? AT(d, 0x20, void *) : 0;
    if (!s || AT(d, 0, unsigned) != AUDIO_DEMUXER || !priv || AT(priv, 0, int) != 1) return 0;
    int fd = AT(s, 0x14, int), got = 0;
    long long at = AT(d, 0x10, long long), back = lseek64(fd, 0, 1);
    if (back < 0) return 0;
    if (lseek64(fd, at, 0) == at) got = read(fd, h, sizeof h);
    lseek64(fd, back, 0);
    if (got < 4 + 2 + 32 + 12) return 0;
    unsigned head = (unsigned)h[0] << 24 | h[1] << 16 | h[2] << 8 | h[3];
    unsigned version = head >> 19 & 3, rate = head >> 10 & 3, mono = (head >> 6 & 3) == 3;
    if (head >> 21 != 0x7ff || version == 1 || (head >> 17 & 3) != 1 || rate == 3) return 0;
    unsigned side = 4 + (version == 3 ? (mono ? 17 : 32) : (mono ? 9 : 17));
    for (unsigned crc = 0; crc <= 2; crc += 2) { /* LAME keeps the fixed offset on a CRC frame, too */
        const unsigned char *x = h + side + crc;
        if (memcmp(x, "Xing", 4) && memcmp(x, "Info", 4)) continue;
        if (!(x[7] & 1)) return 0;
        unsigned frames = (unsigned)x[8] << 24 | x[9] << 16 | x[10] << 8 | x[11], spf = version == 3 ? 1152 : 576;
        if (!frames || frames > 0xffffffffu / spf) return 0;
        return pos > (int)(frames * spf / (rates[rate] >> (version == 3 ? 0 : version == 2 ? 1 : 2))) + 1;
    }
    return 0;
}
/* From 0x4232bc, $a0 mpctx, $s0 position: stock's store if that says so; every register else as it was. */
__asm__(".set push\n.set noreorder\n.set noat\n.globl forced_eof\nforced_eof:\naddiu $sp, $sp, -88\n"
        "sw $ra, 84($sp)\nsw $v0, 16($sp)\nsw $v1, 20($sp)\nsw $a0, 24($sp)\nsw $a2, 28($sp)\nsw $a3, 32($sp)\n"
        "sw $t0, 36($sp)\nsw $t1, 40($sp)\nsw $t2, 44($sp)\nsw $t3, 48($sp)\nsw $t4, 52($sp)\nsw $t5, 56($sp)\n"
        "sw $t6, 60($sp)\nsw $t7, 64($sp)\nsw $t8, 68($sp)\nsw $t9, 72($sp)\nsw $a1, 76($sp)\n"
        "jal forced_eof_c\nmove $a1, $s0\nlw $a0, 24($sp)\nli $a1, 1\n"
        "beqz $v0, 1f\nnop\nsw $a1, 0x14($a0)\n1:\n"
        "lw $ra, 84($sp)\nlw $v0, 16($sp)\nlw $v1, 20($sp)\nlw $a2, 28($sp)\nlw $a3, 32($sp)\n"
        "lw $t0, 36($sp)\nlw $t1, 40($sp)\nlw $t2, 44($sp)\nlw $t3, 48($sp)\nlw $t4, 52($sp)\nlw $t5, 56($sp)\n"
        "lw $t6, 60($sp)\nlw $t7, 64($sp)\nlw $t8, 68($sp)\nlw $t9, 72($sp)\nlw $a1, 76($sp)\n"
        "jr $ra\naddiu $sp, $sp, 88\n.set pop\n");
#endif

/* Stopped, the main thread's idle loop (0x42d994) polled the play flag (PLAY_FLAG) with usleep(10000), and
 * paused, MPlayer's loop (0x42fff0) checked for a command and slept usec_sleep(20000): 100 or 50 wakeups a
 * second, more than everything else on a dark, idle Q2 together. IDLE_WAIT and PAUSE_WAIT make both sleeps
 * futex waits: the stopped loop's on the flag, the paused loop's on cmd_gen. Every command reaches the player
 * through the command thread's handler (0x42be20), which CMD_DONE wraps: when the handler changed the flag
 * (set-songurl, set-nexturl) it wakes the stopped loop, and when it changed anything the paused loop reacts
 * to (cmd_words, and the MPlayer context's state, which mcl-play and mcl-pause set) it moves cmd_gen and
 * wakes that. demo's status queries, several a second, change none and wake nothing. A cmd_gen that moved
 * since the last wait (a command handled while the loop checked) returns at once. Both loops still run
 * their bodies after a wait, so the command thread's heartbeat (0xb10e68) is set again within WAIT_S: that
 * thread clears it each second and exits hciplayer after 6 checks find it clear (0x42d95c).
 * docs/internals.md#battery. ponytail: anything else the loops watch waits for WAIT_S. */
#ifndef PEQ_HOST
#define PLAY_FLAG 0xb10e34u
#define MPCTX_STATE 4 /* mpctx's word: 1 playing (the pause loop returns), 2 paused */
#define WAIT_S 1
_Static_assert(WAIT_S <= 2, "stock watchdog: exit after 6 s without 0xb10e68");
extern int hci_futex(volatile int *addr, int op, int value, const void *timeout);
__asm__(".globl hci_futex\nhci_futex:\nli $v0, 4238\nsyscall\njr $ra\nnop\n");
static const long wait_s[2] = { WAIT_S, 0 };
int hci_idle_wait(unsigned us) {
    (void)us;
    hci_futex((volatile int *)PLAY_FLAG, 128, 0, wait_s); /* FUTEX_WAIT_PRIVATE while 0 */
    return 0;
}
static volatile int cmd_gen;
static int cmd_seen;
int hci_pause_wait(unsigned us) {
    (void)us;
    int g = __atomic_load_n(&cmd_gen, __ATOMIC_ACQUIRE);
    if (g == cmd_seen) {
        hci_futex(&cmd_gen, 128, g, wait_s); /* FUTEX_WAIT_PRIVATE while unchanged */
        g = cmd_gen;
    }
    cmd_seen = g;
    return 0;
}
/* What the two loops read that a command sets: the play flag (first: hci_command wakes the stopped loop on
 * it), the next and stop flags; then the context's state. The context (MPCTX) is always the static 0xaf9e38. */
static const unsigned cmd_words[] = { PLAY_FLAG, 0xb10e6c, 0xb10e70 };
#define CMD_WORDS (sizeof cmd_words / sizeof *cmd_words)
static void cmd_state(int *v) {
    for (unsigned i = 0; i < CMD_WORDS; ++i) v[i] = *(volatile int *)cmd_words[i];
    v[CMD_WORDS] = *(volatile int *)((char *)MPCTX + MPCTX_STATE);
}
void hci_command(void *buf) {
    int before[CMD_WORDS + 1], after[CMD_WORDS + 1], changed = 0;
    cmd_state(before);
    ((void (*)(void *))0x42be20u)(buf);
    cmd_state(after);
    for (unsigned i = 0; i < CMD_WORDS + 1; ++i) changed |= before[i] != after[i];
    if (!changed) return;
    if (after[1] == 1 || after[2] == 1) ao_natural = 0; /* a skip or stop before init: not natural (ao_uninit) */
    if (after[1] == 1 || after[2] == 1 || (!before[0] && after[0])) ao_cut();
    /* A Pause undone before the main thread reached it, a double tap or both inside dac_resume's write: no PCM pause,
     * so no resume will release demo's mute(2). hciplayer's own mute sets the flag first, so a flag still clear says so;
     * if the main thread is just getting there, it mutes after this and its resume releases as ever. A DAC not
     * muted takes this as a no-op: user_mute(0) sets the volume it has. */
    if (before[CMD_WORDS] == 2 && after[CMD_WORDS] == 1 && !DAC_MUTED) DAC_HELPER(0);
    __atomic_add_fetch(&cmd_gen, 1, __ATOMIC_SEQ_CST);
    hci_futex(&cmd_gen, 129, 1, 0); /* FUTEX_WAKE_PRIVATE */
    if (before[0] != after[0]) hci_futex((volatile int *)PLAY_FLAG, 129, 1, 0);
}

/* A file no demuxer knows: the DSF, DFF and SACD ISO checks (0x47b8f0, 0x47a1ac, 0x478444) store the global
 * stop, code 2, on failure, and the probe loop (0x485934) stops on it, before lavf. On the preload thread
 * (0x42b8d0) that stops the song still playing, about 5 s before its end. There the checks now store nothing;
 * the loop still stops, as it reads the flag through $18, which probe_stop points at a word of our own (its
 * epilogue restores $18). Called by the forced path, a check returns to more probing, which reaches the loop.
 * Saving and restoring the flag around the thread's open would lose a real stop landing in it, and the main
 * thread and the FLAC check (flac_sync.c) could read it between. docs/internals.md#a-bad-file-next.
 * ponytail: stores deep in the decoders (0x4ee61c, 0x508370, 0x52f584, 0x5b6260) aren't covered; a preload
 * thread's decode of a next file reaching one still stops the song. */
#define PROBE_LOOP_RA 0x4859a4u /* after the unsafe pass's jalr; the forced path comes back elsewhere */
extern int hci_id(int number);
__asm__(".globl hci_id\nhci_id:\nmove $v0, $a0\nsyscall\njr $ra\nnop\n");
static const int probe_stopped = 1;
void *probe_stop_c(unsigned ra) {
    if (hci_id(4222) != hci_id(4020)) /* gettid != getpid: not the main thread */
        return ra == PROBE_LOOP_RA ? (char *)&probe_stopped - 0xe70 : 0;
    *(volatile int *)0xb10e74u = 2;
    *(volatile int *)0xb10e70u = 1;
    return 0;
}
__asm__(".set push\n.set noreorder\n"
        ".globl probe_stop_dsf\nprobe_stop_dsf:\nb probe_stop\naddiu $sp, $sp, 0xa8\n"
        ".globl probe_stop_iso\nprobe_stop_iso:\nlw $s1, 0x3c($sp)\nb probe_stop\naddiu $sp, $sp, 0x60\n"
        ".globl probe_stop_dff\nprobe_stop_dff:\naddiu $sp, $sp, 0xc0\n"
        "probe_stop:\naddiu $sp, $sp, -24\nsw $ra, 20($sp)\nmove $a0, $ra\njal probe_stop_c\nnop\n"
        "lw $ra, 20($sp)\nbeqz $v0, 1f\naddiu $sp, $sp, 24\nmove $s2, $v0\n"
        "1:\njr $ra\nmove $v0, $zero\n.set pop\n");

/* The same store in the checks' other shapes. A SACD ISO's TOC with no tracks (0x478b5c), an AAC scan that found no
 * frame (0x48018c) and an ASF with no streams (0x4826bc, in its open, after its check took the file) each store the
 * stop and return. stop_base gives a store's base: stock's on the main thread, on any other a word of our own, with
 * the saved $18 of the frame the probe loop's call made pointed at probe_stopped so the loop still gives up.
 * The ISO's fill (0x46c184, no track data after its retries) runs on the thread when its decoder starts; there's
 * no loop to stop. 0x42a47c is exit_sighandler, the process's own crash: left. */
#define PROBE_OPEN_RA 0x4859f0u
static int stop_sink[2];
char *stop_base(unsigned ra, void **saved18) {
    if (hci_id(4222) == hci_id(4020)) return (char *)0xb10000u;
    if (ra == PROBE_LOOP_RA || ra == PROBE_OPEN_RA) *saved18 = (char *)&probe_stopped - 0xe70;
    return (char *)stop_sink - 0xe70;
}
/* ra, s2: the slots of the return address and $18 the probe loop's call saved, from this function's $sp. */
#define STOP_SITE(name, ra, s2) \
    ".globl " name "\n" name ":\naddiu $sp, $sp, -24\nsw $ra, 20($sp)\nlw $a0, " #ra " + 24($sp)\n" \
    "addiu $a1, $sp, " #s2 " + 24\njal stop_base\nnop\nlw $ra, 20($sp)\naddiu $sp, $sp, 24\njr $ra\nli $v1, 2\n"
__asm__(".set push\n.set noreorder\n"
        STOP_SITE("stop_iso", 0x5c, 0x40)
        STOP_SITE("stop_aac", 0x78 + 0x6c, 0x78 + 0x50)
        STOP_SITE("stop_asf", 0xb0 + 0x2c, 0xb0 + 0x28)
        /* From 0x46c184 by j, $v1 2, epilogue done but for the $sp in the jr's delay slot. */
        ".globl stop_fill\nstop_fill:\nli $v0, 4222\nsyscall\nmove $t0, $v0\nli $v0, 4020\nsyscall\nbne $v0, $t0, 1f\nnop\n"
        "lui $v0, 0xb1\nli $t0, 2\nsw $t0, 0xe74($v0)\nli $t0, 1\nsw $t0, 0xe70($v0)\n"
        "1:\nmove $v0, $zero\njr $ra\naddiu $sp, $sp, 0x50\n.set pop\n");
#endif
