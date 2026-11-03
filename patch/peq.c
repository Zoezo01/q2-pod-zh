#include "peq.h"

#ifdef PEQ_HOST
int __isoc99_sscanf(const char *, const char *, ...); /* stdio.h may hide it behind C23's sscanf redirect */
#endif

static int between(double x, double lo, double hi) {
    return __builtin_isfinite(x) && x >= lo && x <= hi;
}

void peq_default(peq_preset *p) {
    static const double frequencies[10] = {31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000};
    memset(p, 0, sizeof(*p));
    p->count = 10;
    p->bypass = 1;
    for (int i = 0; i < PEQ_BANDS; ++i) {
        p->bands[i].frequency = i < 10 ? frequencies[i] : 1000;
        p->bands[i].q = 1.41; /* one octave wide, as graphic EQs use */
    }
}

static int valid_band(const peq_band *b) {
    return b->enabled >= 0 && b->enabled <= 3 && b->type >= 0 && b->type <= 2 &&
           between(b->frequency, 20, 20000) && between(b->gain, -24, 24) && between(b->q, 0.1, 10);
}

int peq_valid(const peq_preset *p) {
    if (!p || p->count < 0 || p->count > PEQ_BANDS || (p->bypass != 0 && p->bypass != 1) ||
        !between(p->preamp, -60, 24) || !between(p->balance, -12, 12) || (unsigned)p->automatic > 2)
        return 0;
    for (int i = 0; i < p->count; ++i) if (!valid_band(&p->bands[i])) return 0;
    return 1;
}

static int error_at(peq_error *e, unsigned line, const char *reason) {
    if (e) { e->line = line; e->reason = reason; }
    return 0;
}

/* Decimal only (comma accepted as the mark, like APO); no hex floats, expressions, NaN or infinity.
 * libc reads the value, and the token must be consumed whole. demo's GOT carries __isoc99_sscanf,
 * not strtod (tools/peq.py LIBC). */
int peq_number(const char *s, double *out) {
    char buf[PEQ_LINE_LIMIT + 1];
    unsigned n = 0;
    while (s[n]) {
        if (n == PEQ_LINE_LIMIT) return 0;
        char c = s[n];
        if (!(c >= '0' && c <= '9') && c != '+' && c != '-' && c != '.' && c != ',' &&
            c != 'e' && c != 'E') return 0;
        buf[n] = c == ',' ? '.' : c;
        ++n;
    }
    buf[n] = 0;
    const char *p = buf + (buf[0] == '+' || buf[0] == '-');
    if (!n || (p[0] == '0' && (p[1] | 32) == 'x')) return 0; /* no hex floats */
    int len = 0;
    double v;
    if (__isoc99_sscanf(buf, "%lf%n", &v, &len) != 1 || len != (int)n || !__builtin_isfinite(v))
        return 0;
    *out = v;
    return 1;
}

int peq_parse(const char *text, unsigned size, peq_preset *out, peq_error *error) {
    peq_preset p;
    peq_default(&p);
    p.count = 0;
    p.bypass = 0;
    unsigned pos = 0, line = 0, preamp_line = 0;
    int scope = 1; /* APO Channel: 1 both, 2 left, 3 right, as peq_band.enabled */
    double side[4] = {0}; /* Preamp under Channel: L or R */
    if (size > PEQ_FILE_LIMIT) return error_at(error, 1, "file exceeds 16384 bytes");
    if (size >= 3 && (unsigned char)text[0] == 239 && (unsigned char)text[1] == 187 &&
        (unsigned char)text[2] == 191) pos = 3;
    while (pos < size) {
        char buf[PEQ_LINE_LIMIT + 1], *t[16];
        unsigned length = 0;
        ++line;
        while (pos < size && text[pos] != '\n') {
            if (length == PEQ_LINE_LIMIT) return error_at(error, line, "line exceeds 512 bytes");
            if (!text[pos]) return error_at(error, line, "embedded NUL");
            buf[length++] = text[pos++];
        }
        if (pos < size) ++pos;
        buf[length] = 0;
        int n = 0;
        char *s = buf;
        while (*s && *s != '#') {
            if (*s == ' ' || *s == '\t' || *s == '\r') { ++s; continue; }
            if (n == 16) return error_at(error, line, "unsupported filter form");
            /* A colon is a separate token, even in "Filter 1:" and "Preamp:". */
            if (*s == ':') { t[n++] = ":"; ++s; continue; }
            t[n++] = s;
            while (*s && *s != ' ' && *s != '\t' && *s != '\r' && *s != ':' && *s != '#') ++s;
            char delimiter = *s;
            if (*s) *s++ = 0;
            if (delimiter == ':') {
                if (n == 16) return error_at(error, line, "unsupported filter form");
                t[n++] = ":";
            }
            if (delimiter == '#') break;
        }
        if (!n) continue;
        if (!strcmp(t[0], "Preamp")) {
            double gain;
            if (n != 4 || strcmp(t[1], ":") || strcmp(t[3], "dB"))
                return error_at(error, line, "expected Preamp: <gain> dB");
            if (!peq_number(t[2], &gain) || !between(gain, -60, 24))
                return error_at(error, line, "preamp outside -60..24 dB or invalid number");
            if (scope == 1) p.preamp += gain;
            else side[scope] += gain;
            preamp_line = line;
            continue;
        }
        if (!strcmp(t[0], "Channel")) {
            int l = 0, r = 0;
            if (n < 3 || strcmp(t[1], ":")) return error_at(error, line, "expected Channel: L, R or all");
            for (int i = 2; i < n; ++i) {
                if (!strcmp(t[i], "L")) l = 1;
                else if (!strcmp(t[i], "R")) r = 1;
                else if (!strcmp(t[i], "all")) l = r = 1;
                else return error_at(error, line, "only channels L, R and all are supported");
            }
            scope = l && r ? 1 : l ? 2 : 3;
            continue;
        }
        if (strcmp(t[0], "Filter")) return error_at(error, line, "unsupported command");
        int k = 1;
        if (n > 1 && strcmp(t[1], ":")) {
            for (s = t[1]; *s; ++s) if (*s < '0' || *s > '9')
                return error_at(error, line, "invalid filter label");
            ++k;
        }
        if (n <= k || strcmp(t[k++], ":")) return error_at(error, line, "expected Filter [number]:");
        if (n - k == 2 && !strcmp(t[k+1], "None")) continue; /* REW/APO empty slot */
        if (n - k != 8 && n - k != 10) return error_at(error, line, "unsupported filter form");
        peq_band b = {0, 0, 0, 0, 0.7071067811865476};
        if (!strcmp(t[k], "ON")) b.enabled = scope;
        else if (strcmp(t[k], "OFF")) return error_at(error, line, "expected ON or OFF");
        ++k;
        if (!strcmp(t[k], "PK") || !strcmp(t[k], "PEQ")) b.type = 0;
        else if (!strcmp(t[k], "LS") || !strcmp(t[k], "LSC")) b.type = 1;
        else if (!strcmp(t[k], "HS") || !strcmp(t[k], "HSC")) b.type = 2;
        else return error_at(error, line, "unsupported filter type");
        int corner = !t[k][2]; /* APO: LS/HS take a corner frequency, LSC/HSC the centre */
        ++k;
        if (strcmp(t[k], "Fc") || strcmp(t[k+2], "Hz") || strcmp(t[k+3], "Gain") ||
            strcmp(t[k+5], "dB")) return error_at(error, line, "expected Fc <Hz> Hz Gain <dB> dB");
        if (!peq_number(t[k+1], &b.frequency) || !peq_number(t[k+4], &b.gain))
            return error_at(error, line, "invalid frequency or gain number");
        k += 6;
        if (k < n) {
            if (strcmp(t[k], "Q") || !peq_number(t[k+1], &b.q))
                return error_at(error, line, "expected Q <number>");
        } else if (!b.type) return error_at(error, line, "peaking filter requires Q");
        if (b.type && valid_band(&b)) {
            /* Match Equalizer APO: no Q means slope 0.9 at Fc; LS/HS with Q shift the corner to the centre. */
            double a = pow(10, b.gain / 40), ab = a + 1 / a;
            if (k >= n) b.q = 1 / __builtin_sqrt(ab * (1 / 0.9 - 1) + 2);
            else if (corner) {
                double f = pow(10, __builtin_fabs(b.gain) / 80 * ((1 / (b.q * b.q) - 2) / ab + 1));
                b.frequency = b.type == 1 ? b.frequency * f : b.frequency / f;
                /* The shift can leave 20..20000 Hz; clamp instead of rejecting the whole file. */
                if (b.frequency > 20000) b.frequency = 20000;
                if (b.frequency < 20) b.frequency = 20;
            }
        }
        if (!valid_band(&b)) {
            if (!b.enabled) continue; /* APO ignores OFF filters; exporters fill blank slots with Fc 0 Q 0 */
            return error_at(error, line, "range: 20..20000 Hz, -24..24 dB, Q 0.1..10");
        }
        if (p.count == PEQ_BANDS) return error_at(error, line, "more than 30 bands");
        p.bands[p.count++] = b;
    }
    /* Per-channel preamps become the louder one's common preamp plus a balance that turns the other down. */
    p.preamp += side[2] > side[3] ? side[2] : side[3];
    p.balance = side[3] - side[2];
    if (!between(p.preamp, -60, 24)) return error_at(error, preamp_line, "summed preamp outside -60..24 dB");
    if (!between(p.balance, -12, 12)) return error_at(error, preamp_line, "L/R preamp differ by more than 12 dB");
    p.automatic = preamp_line ? 2 : 1; /* the file's preamp, as if picked; without one, Auto */
    *out = p; /* Commit only after the complete file validates. */
    return 1;
}

int peq_import_file(const char *path, peq_preset *out, peq_error *error) {
    char *text = calloc(1, PEQ_FILE_LIMIT + 1);
    if (!text) return error_at(error, 1, "out of memory");
    void *f = fopen(path, "rb");
    if (!f) { free(text); return error_at(error, 1, "cannot open file"); }
    unsigned n = fread(text, 1, PEQ_FILE_LIMIT + 1, f);
    int bad = ferror(f);
    if (fclose(f)) bad = 1;
    int ok = bad ? error_at(error, 1, "file read failed") : peq_parse(text, n, out, error);
    free(text);
    return ok;
}

int peq_load(const char *path, peq_preset *out) {
    struct { char magic[8]; peq_preset preset; } file;
    void *f = fopen(path, "rb");
    if (!f) return 0;
    char extra;
    memset(file.magic, 0, sizeof(file.magic));
    peq_default(&file.preset); /* bands a v1 file lacks keep their defaults */
    unsigned n = fread(&file, 1, sizeof(file), f);
    int ok = fread(&extra, 1, 1, f) == 0 && !ferror(f);
    if (fclose(f)) ok = 0;
    /* Q2PEQ01 predates balance and held ten bands; Q2PEQ02 before 1.3.6 lacks automatic. */
    unsigned want =
        !memcmp(file.magic, "Q2PEQ01", 8) ? 8 + __builtin_offsetof(peq_preset, bands[10]) : 0;
    if (!memcmp(file.magic, "Q2PEQ02", 8)) want = n == sizeof(file) ? n : 8 + PEQ_PRESET_V2;
    if (!ok || !want || n != want || !peq_valid(&file.preset)) return 0;
    *out = file.preset;
    return 1;
}

/* magic (8 bytes) then data, fsynced under path.tmp and renamed over path. */
static int save_file(const char *path, const char *magic, const void *data, unsigned size) {
    char tmp[600];
    if (strlen(path) > 580) return 0;
    snprintf(tmp, sizeof(tmp), "%s.tmp", path);
    void *f = fopen(tmp, "wb");
    if (!f) return 0;
    int ok = fwrite(magic, 1, 8, f) == 8 && fwrite(data, 1, size, f) == size;
    if (fflush(f) || fsync(fileno(f))) ok = 0;
    if (fclose(f)) ok = 0;
    if (ok && !rename(tmp, path)) return 1;
    unlink(tmp);
    return 0;
}

int peq_save(const char *path, const peq_preset *p, int replace) {
    if (!peq_valid(p)) return 0;
    if (!replace && !access(path, 0)) return 2;
    return save_file(path, "Q2PEQ02", p, sizeof(*p));
}

/* The 3EQ's fixed filters at gain dB each: a 155 Hz low shelf, a peak at 900 Hz to cut or 1750 Hz to
 * boost (Listener's reading of Apple's AirPods EQ; Q 0.707 and the frequencies within its ranges are
 * Q2 Pod's choices), a 5 kHz high shelf. A zero gain is an identity band. */
void eq3_bands(peq_preset *p, const double gain[3]) {
    static const int types[3] = {1, 0, 2};
    static const double frequencies[3] = {155, 1750, 5000};
    memset(p, 0, sizeof(*p));
    p->count = 3;
    for (int i = 0; i < 3; ++i)
        p->bands[i] = (peq_band){1, types[i], i == 1 && gain[1] < 0 ? 900 : frequencies[i], gain[i], 0.707};
}

static int eq_valid(const eq_state *s) {
    return s->mode >= EQ_OFF && s->mode <= EQ_PEQ && (s->hold == 0 || s->hold == 1) && peq_valid(&s->peq) &&
           peq_valid(&s->eq3) && s->eq3.count == 3;
}

/* Q2PEQ03, else a Q2PEQ01/02 preset from before 3EQ: its PEQ, on unless bypassed, and a flat 3EQ. */
int eq_load(eq_state *s) {
    static const double flat[3];
    struct { char magic[8]; eq_state s; } file;
    memset(s, 0, sizeof(*s));
    peq_default(&s->peq);
    eq3_bands(&s->eq3, flat);
    void *f = fopen(PEQ_ACTIVE, "rb");
    if (!f) return 0;
    char extra;
    memset(&file, 0, sizeof(file));
    unsigned n = fread(&file, 1, sizeof(file), f), v2 = 8 + 8 + 2 * PEQ_PRESET_V2;
    int ok = (n == sizeof(file) || n == v2) && fread(&extra, 1, 1, f) == 0 && !ferror(f);
    if (fclose(f)) ok = 0;
    /* 1.3.5's: presets without automatic, which the zeroed tail leaves 0; demo has no memmove. */
    if (n == v2) {
        peq_preset eq3;
        memcpy(&eq3, (char *)&file.s.peq + PEQ_PRESET_V2, sizeof(eq3));
        file.s.eq3 = eq3;
        file.s.peq.automatic = file.s.peq.spare = 0;
    }
    if (ok && !memcmp(file.magic, "Q2PEQ03", 8) && eq_valid(&file.s)) {
        *s = file.s;
        return 1;
    }
    if (!peq_load(PEQ_ACTIVE, &s->peq)) return 0;
    s->mode = s->peq.bypass ? EQ_OFF : EQ_PEQ;
    return 1;
}

int eq_save(const eq_state *s) {
    return eq_valid(s) && save_file(PEQ_ACTIVE, "Q2PEQ03", s, sizeof(*s));
}

/* What plays: the mode's preset, bypassed when Off, or with hold the 3EQ's preamp alone. Only PEQ
 * plays Auto (1: the player's per-rate cut), unless picked: never picked (0) is Auto, as the editor
 * takes it. 3EQ (0) and the hold keep their saved preamp. */
void eq_effective(const eq_state *s, peq_preset *out) {
    *out = s->mode == EQ_3 ? s->eq3 : s->peq;
    if (s->mode == EQ_PEQ ? !out->automatic : out->automatic == 1)
        out->automatic = s->mode == EQ_PEQ;
    out->bypass = s->mode == EQ_OFF && !s->hold;
    if (s->mode == EQ_OFF && s->hold) out->count = 0, out->preamp = s->eq3.preamp, out->balance = 0;
}

int peq_load_active(peq_preset *p) {
    eq_state s;
    int ok = eq_load(&s);
    eq_effective(&s, p);
    return ok;
}

/* The preamp, linear, on every channel, then balance turning one side down. */
static void level(peq_engine *e, double gain, double balance) {
    for (int ch = 0; ch < PEQ_CHANNELS; ++ch) e->gain[ch] = gain;
    if (balance > 0) e->gain[0] *= pow(10, -balance / 20);
    if (balance < 0) e->gain[1] *= pow(10, balance / 20);
}

/* RBJ peaking and Q-form shelves. All design work is outside the PCM callback. */
int peq_compile(const peq_preset *p, int rate, peq_engine *out) {
    peq_engine e;
    memset(&e, 0, sizeof(e));
    if (!peq_valid(p) || rate < 8000 || rate > 384000) return 0;
    level(&e, pow(10, p->preamp / 20), p->balance);
    e.bypass = p->bypass;
    for (int i = 0; i < PEQ_BANDS; ++i) {
        e.c[i].b0 = 1;
        if (i >= p->count || !p->bands[i].enabled || !p->bands[i].gain) continue;
        e.used = i + 1;
        if (p->bands[i].enabled > 1) e.only[i] = p->bands[i].enabled - 1;
        const peq_band *b = &p->bands[i];
        if (b->frequency >= rate * 0.5) continue; /* not representable at this rate; skip only this band */
        double a = pow(10, b->gain / 40), w = 6.283185307179586 * b->frequency / rate;
        double c = cos(w), alpha = sin(w) / (2 * b->q), r = 2 * __builtin_sqrt(a) * alpha;
        double b0, b1, b2, a0, a1, a2;
        if (!b->type) {
            b0 = 1 + alpha * a; b1 = -2 * c; b2 = 1 - alpha * a;
            a0 = 1 + alpha / a; a1 = -2 * c; a2 = 1 - alpha / a;
        } else if (b->type == 1) {
            b0 = a * ((a+1) - (a-1)*c + r); b1 = 2*a*((a-1) - (a+1)*c);
            b2 = a*((a+1) - (a-1)*c - r); a0 = (a+1) + (a-1)*c + r;
            a1 = -2*((a-1) + (a+1)*c); a2 = (a+1) + (a-1)*c - r;
        } else {
            b0 = a*((a+1) + (a-1)*c + r); b1 = -2*a*((a-1) + (a+1)*c);
            b2 = a*((a+1) + (a-1)*c - r); a0 = (a+1) - (a-1)*c + r;
            a1 = 2*((a-1) - (a+1)*c); a2 = (a+1) - (a-1)*c - r;
        }
        e.c[i] = (peq_coeff){b0/a0, b1/a0, b2/a0, a1/a0, a2/a0};
    }
    *out = e;
    return 1;
}

/* |H|^2 of the engine's left and right chains, without preamp or balance. */
void peq_response(const peq_engine *e, int rate, double f, double *l, double *r) {
    double w = 6.283185307179586 * f / rate, c1 = cos(w), s1 = sin(w), c2 = 2 * c1 * c1 - 1,
           s2 = 2 * s1 * c1;
    *l = *r = 1;
    for (int i = 0; i < e->used; ++i) { /* bands past used are identity */
        const peq_coeff *q = &e->c[i];
        double nr = q->b0 + q->b1 * c1 + q->b2 * c2, ni = q->b1 * s1 + q->b2 * s2;
        double dr = 1 + q->a1 * c1 + q->a2 * c2, di = q->a1 * s1 + q->a2 * s2;
        double m = (nr * nr + ni * ni) / (dr * dr + di * di);
        if (e->only[i] != 2) *l *= m; /* only: 1 left, 2 right */
        if (e->only[i] != 1) *r *= m;
    }
}

/* The loudest |H|^2 of e (p compiled at rate), either channel, at least 1 (Auto never boosts) and
 * at most 60 dB. Includes DC, Nyquist and each band's centre: near Nyquist a peak can be narrower
 * than the grid. shortcut: sampled steady-state peaks, not a transient or intersample bound. */
static double peak(const peq_engine *e, const peq_preset *p, int rate) {
    double nyquist = rate * 0.5, best = 1;
    for (int k = -2; k < 1024 + p->count; ++k) {
        double f = k == -2    ? 0
                   : k == -1  ? nyquist
                   : k < 1024 ? pow(nyquist, k / 1023.0)
                              : p->bands[k - 1024].frequency;
        if (f >= nyquist && k != -1) continue;
        double l, r;
        peq_response(e, rate, f, &l, &r);
        if (l > best) best = l;
        if (r > best) best = r;
    }
    return best < 1.0000230261 ? 1 : best > 1e6 ? 1e6 : best; /* under 0.0001 dB is none */
}

double peq_auto(const peq_preset *p, int rate) {
    peq_engine e;
    if (!peq_compile(p, rate, &e)) return p->preamp;
    double v = peak(&e, p, rate);
    return v > 1 ? -decibels(v) : 0; /* no boost: +0, not -0.0 on screen */
}

/* The player's engine: Auto (1) takes the cut for this rate's own response, from the coefficients
 * just designed, in place of the saved number (the editor's 48 kHz figure). */
static int prepare(const peq_preset *p, int rate, peq_engine *e) {
    if (!peq_compile(p, rate, e)) return 0;
    if (p->automatic == 1) level(e, pow(peak(e, p, rate), -0.5), p->balance);
    return 1;
}

void peq_reset(peq_dsp *d, int rate, int channels, const peq_preset *p) {
    memset(d, 0, sizeof(*d));
    d->rate = rate;
    d->channels = channels;
    d->ramp_length = rate / 50; /* 20 ms */
    if (!prepare(p, rate, &d->current)) d->current.bypass = 1;
}

int peq_update(peq_dsp *d, const peq_preset *p) {
    if (!prepare(p, d->rate, &d->pending)) return 0;
    d->waiting = 1; /* Latest complete update wins; never interrupt an in-flight crossfade. */
    return 1;
}

/* Identity coefficients: with zero memory the band passes x through unchanged. */
static inline __attribute__((always_inline)) int identity(const peq_coeff *c) { return c->b0 == 1 && !c->b1 && !c->b2 && !c->a1 && !c->a2; }

/* The bands sample can act on, in order: identity bands with zero memory stay so until an update. */
static int live(const peq_engine *e, int *list) {
    int n = 0;
    for (int i = 0; i < e->used; ++i) {
        int on = e->only[i] || !identity(&e->c[i]);
        for (int ch = 0; ch < PEQ_CHANNELS && !on; ++ch) on = e->z[ch][i][0] || e->z[ch][i][1];
        if (on) list[n++] = i;
    }
    return n;
}

static double sample(peq_engine *e, const int *list, int n, double x, int ch) {
    if (e->bypass) return x;
    for (int k = 0; k < n; ++k) {
        int i = list[k];
        const peq_coeff *c = &e->c[i];
        double *z = e->z[ch][i];
        /* The other channel's band: drop memory carried from a both-channel version. */
        if (e->only[i] && e->only[i] != ch + 1) { z[0] = z[1] = 0; continue; }
        /* Still checked per sample: a band can drain to idle mid-block. */
        if (identity(c) && !z[0] && !z[1]) continue;
        /* Flush |y| < ~2e-34 to zero: decaying tails would otherwise reach denormals, which MIPS FPUs trap on. */
        double y = c->b0 * x + z[0] + 1e-18 - 1e-18;
        z[0] = c->b1 * x - c->a1 * y + z[1];
        z[1] = c->b2 * x - c->a2 * y;
        x = y;
    }
    return x * e->gain[ch]; /* after the cascade, so filter memory is independent of preamp */
}

void peq_process(peq_dsp *d, float *audio, unsigned frames) {
    if (!audio || d->channels < 1 || d->channels > PEQ_CHANNELS) return;
    if (d->current.bypass && !d->ramp && !d->waiting) return; /* steady bypass leaves the whole block untouched */
    int cur[PEQ_BANDS], nxt[PEQ_BANDS], nc = live(&d->current, cur), nn = d->ramp ? live(&d->next, nxt) : 0;
    for (unsigned i = 0; i < frames; ++i) {
        if (!d->ramp && d->waiting) {
            d->next = d->pending;
            d->waiting = 0;
            d->ramp = d->ramp_length;
            /* Carry filter memory over so unchanged bands and preamp-only edits crossfade without a transient. */
            if (!d->current.bypass) memcpy(d->next.z, d->current.z, sizeof(d->next.z));
            /* Bands dropped from the end still run until their carried memory drains. */
            for (int b = d->next.used; b < d->current.used; ++b)
                for (int ch = 0; ch < PEQ_CHANNELS; ++ch) if (d->next.z[ch][b][0] || d->next.z[ch][b][1]) d->next.used = b + 1;
            nn = live(&d->next, nxt);
        }
        for (int ch = 0; ch < d->channels; ++ch, ++audio) {
            if (!d->ramp && d->current.bypass) continue; /* bit-exact steady bypass */
            double x = *audio;
            if (!__builtin_isfinite(x)) x = 0;
            double y = sample(&d->current, cur, nc, x, ch);
            if (d->ramp) {
                double wet = sample(&d->next, nxt, nn, x, ch);
                double mix = (double)(d->ramp_length - d->ramp + 1) / d->ramp_length;
                y += (wet - y) * mix;
            }
            /* Keep float headroom for downstream volume; the output converter owns full-scale clipping. */
            *audio = (float)y;
        }
        if (d->ramp && !--d->ramp) d->current = d->next, nc = nn, memcpy(cur, nxt, sizeof(cur));
    }
}

double decibels(double power) { return 10 * log(power) / 2.302585092994046; } /* no log10 in demo's GOT */

long long now_ns(void) {
    struct { long s, ns; } t; /* struct timespec */
    clock_gettime(1, (void *)&t); /* CLOCK_MONOTONIC: the same clock in hciplayer and demo */
    return t.s * 1000000000LL + t.ns;
}
