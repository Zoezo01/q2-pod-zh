/* Show Sample Rate (docs/internals.md#sample-rate-in-the-status-bar): the rate the Q2 sends to its
 * jacks or a USB DAC, read from the open playback PCM, as the status bar's small text: "44.1k",
 * "192k", "DSD64". The parsing and the text are plain C, which test/unit/test_samplerate.py
 * builds on the host (-DPEQ_HOST). */
#ifdef PEQ_HOST
#include "peq.h"
#else
#include "navigation.h"
#endif

typedef struct {
    unsigned rate, bits, dsd; /* dsd: 64, 128, 256, 512, else 0; bits: 0 when unknown */
} sr_out;

static unsigned sr_number(const char *s) {
    unsigned n = 0;
    while (*s >= '0' && *s <= '9') n = n * 10 + (unsigned)(*s++ - '0');
    return n;
}

/* The value after "key: " on its own line of an hw_params file, or 0. */
static const char *sr_field(const char *text, const char *key) {
    unsigned k = (unsigned)strlen(key);
    for (const char *line = text; *line;) {
        if (!strncmp(line, key, k) && line[k] == ':' && line[k + 1] == ' ') return line + k + 2;
        while (*line && *line != '\n') ++line; /* the next line (the payload imports no strchr) */
        if (*line) ++line;
    }
    return 0;
}

/* An ALSA sample format's bits, by name: S16_LE 16, S24_3LE 24, S32_LE 32, a DSD_U8/U16/U32's
 * container width as a negative (its DSD), 0 for one without a fixed integer width (FLOAT_LE,
 * MU_LAW). */
static int sr_format_bits(const char *f) {
    if (!strncmp(f, "DSD_U", 5)) return -(int)sr_number(f + 5);
    if ((*f == 'S' || *f == 'U') && f[1] >= '1' && f[1] <= '9') return (int)sr_number(f + 1);
    return 0;
}

/* An open PCM's rate and bits from its hw_params text: 1, else 0 (closed, or no rate). */
int sr_parse_hw_params(const char *text, sr_out *out) {
    const char *rate = sr_field(text, "rate"), *format = sr_field(text, "format");
    out->rate = rate ? sr_number(rate) : 0, out->bits = 0, out->dsd = 0;
    if (!out->rate) return 0;
    int bits = format ? sr_format_bits(format) : 0;
    if (bits < 0) /* native DSD: the rate counts containers of -bits one-bit samples */
        out->dsd = out->rate * (unsigned)-bits / 44100;
    else
        out->bits = (unsigned)bits;
    return 1;
}

/* DoP: a DSD song sent as 24-bit PCM at 176.4 kHz or more carries 16 DSD bits a frame. */
void sr_dop(sr_out *o, int song_is_dsd) {
    if (song_is_dsd && !o->dsd && o->rate >= 176400 && o->bits >= 24) o->dsd = o->rate * 16 / 44100;
}

/* The bar's text while mode is on, the rate alone as a USB DAC's display shows it (the bits are the
 * container's, not the music's), cut to size: its length, else 0. */
int sr_format(const sr_out *o, int mode, char *buf, unsigned size) {
    char t[32];
    unsigned n = 0;
    if (!size) return 0;
    if (mode && o->dsd)
        n = (unsigned)snprintf(t, sizeof t, "DSD%u", o->dsd);
    else if (mode && o->rate) {
        unsigned k = o->rate / 1000, frac = o->rate % 1000, digits = 3;
        while (frac && frac % 10 == 0) frac /= 10, --digits;
        n = frac ? (unsigned)snprintf(t, sizeof t, "%u.%0*uk", k, (int)digits, frac)
                 : (unsigned)snprintf(t, sizeof t, "%uk", k);
    }
    if (n >= size) n = size - 1;
    memcpy(buf, t, n);
    buf[n] = 0;
    return (int)n;
}

/***************************************************************************************************
 * The live reading
 **************************************************************************************************/

#define SR_EVERY_MS 500 /* a read at most this often while shown */
#define SR_CARDS 8

static struct {
    unsigned at; /* the last read's time, never 0 once read */
    int mode;
    char text[16];
} sr __attribute__((section(".scratch")));

#ifdef PEQ_HOST
static unsigned sr_clock(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (unsigned)(t.tv_sec * 1000 + t.tv_nsec / 1000000);
}
static int sr_bluetooth(void) { return 0; }
static int sr_song_is_dsd(void) { return 0; }
#else
#define sr_clock time_now_ms
/* Bluetooth shows nothing, as a USB DAC's display shows only what comes over its cable. */
static int sr_bluetooth(void) { return mclGetOutputWay() == 1; }
/* The playing song's file is a DSF or DSDIFF, so a fast enough PCM is DoP (crossfade.c's test). */
static int sr_song_is_dsd(void) {
    void *q = P(mcl_pdeqplaylist, 0);
    unsigned pos = (unsigned)I(MCL_POS, 0);
    const char *p = q && pos < deque_size(q) ? P(deque_at(q, pos), REC_PATH) : 0,
               *e = p ? strrchr(p, '.') : 0;
    return e && (!strcasecmp(e, ".dsf") || !strcasecmp(e, ".dff"));
}

#endif

/* The open playback PCM's: one RUNNING, else the first open (prepared or paused), cards 0 to 7. */
static int sr_alsa(sr_out *o) {
    int found = 0;
    for (int card = 0; card < SR_CARDS && found < 2; ++card)
        for (int dev = 0; dev < 8 && found < 2; ++dev) {
            char path[96], text[512];
            snprintf(path, sizeof path, PEQ_ROOT "/proc/asound/card%d/pcm%dp/sub0/hw_params", card,
                     dev);
            int fd = open(path, 0x80000); /* O_RDONLY | O_CLOEXEC, MIPS */
            if (fd < 0) {
                if (!dev) break; /* no such card: the next */
                continue;
            }
            int n = (int)read(fd, text, sizeof text - 1);
            close(fd);
            text[n > 0 ? n : 0] = 0;
            sr_out here;
            if (!sr_parse_hw_params(text, &here)) continue;
            memcpy(path + strlen(path) - 9, "status", 7); /* hw_params -> status */
            fd = open(path, 0x80000);
            n = fd < 0 ? 0 : (int)read(fd, text, sizeof text - 1);
            if (fd >= 0) close(fd);
            text[n > 0 ? n : 0] = 0;
            int running = strstr(text, "state: RUNNING") != 0;
            if (!found || running) *o = here;
            found = running ? 2 : 1;
        }
    return found != 0;
}

/* Forget the last read, so the next sr_now reads again (a setting change, the tests). */
void sr_forget(void) { sr.at = 0, sr.text[0] = 0; }

/* The bar's text now while mode (Show Sample Rate) is on, none on Bluetooth: read at most every
 * SR_EVERY_MS. Its length, 0 for none. */
int sr_now(int mode, char *buf, unsigned size) {
    if (!size) return 0;
    buf[0] = 0;
    if (!mode) return 0;
    unsigned now = sr_clock();
    if (!sr.at || now - sr.at >= SR_EVERY_MS || mode != sr.mode) {
        sr_out o = { 0, 0, 0 };
        int ok = !sr_bluetooth() && sr_alsa(&o);
        if (ok) sr_dop(&o, sr_song_is_dsd());
        if (!ok) o.rate = 0, o.dsd = 0;
        sr_format(&o, mode, sr.text, sizeof sr.text);
        sr.at = now | !now, sr.mode = mode;
    }
    unsigned n = (unsigned)strlen(sr.text);
    if (n >= size) n = size - 1;
    memcpy(buf, sr.text, n);
    buf[n] = 0;
    return (int)n;
}

/***************************************************************************************************
 * The status bar's label
 **************************************************************************************************/

int sr_setting
    __attribute__((section(".scratch"))); /* POD_SAMPLERATE's value, pod_settings reads it */

#ifndef PEQ_HOST
#define SR_PX 14 /* Minimal's and Modern's: smaller than EQ's 16 and the icons' */

/* The text's width at px, by character, a little over the face's so it hides rather than crowds:
 * there's no canvas to measure with where bar_sync runs. */
static int sr_text_width(const char *s, int px) {
    int w = 0;
    for (; *s; ++s)
        w += *s == '.' ? 4 : *s == '/' ? 5 : *s == 'k' ? 7 : *s >= 'A' && *s <= 'Z' ? 10 : 8;
    return w * px / SR_PX;
}

/* bar_sync, each bar paint: the text starting at x (left: after the play state and EQ) or ending at
 * x (else: left of the right-hand group), shown only within room, the px bar_sync leaves free after
 * everything else in the bar, and only while the bar's centre holds the clock: a page title there
 * (Time in Title off) is wider than the clock and reaches the rate's place. Made the first time
 * there is one. A change repaints the bar after the frame, as addon_bar.c's icon does. */
static char sr_synced[16]
    __attribute__((section(".scratch"))); /* the text bar_rate_sync last took */

/* ringnav_showface, each second: whether the rate now differs from what the bar last took, so a
 * still page repaints its bar for it (the bar paints by itself only at the clock's minute). */
int bar_rate_stale(void) {
    char text[16] = { 0 };
    sr_now(pod_value(POD_SAMPLERATE), text, sizeof text);
    return strcmp(text, sr_synced) != 0;
}

void bar_rate_sync(int left, int x, int room) {
    static widget_t *label __attribute__((section(".scratch")));
    static int aligned __attribute__((section(".scratch"))); /* 1 left, 2 right, 0 not yet */
    char text[16] = { 0 }; /* whole, as memcpy copies all of it into sr_synced */
    int n = sr_now(pod_value(POD_SAMPLERATE), text, sizeof text);
    memcpy(sr_synced, text, sizeof sr_synced);
    /* Classic's text in its bar's face and size, as its title and clock; else SR_PX */
    int px = left ? SR_PX : theme()->font_px[ROLE_BAR] ?: SR_PX, w = sr_text_width(text, px);
    int changed = 0, at = left ? x : x - w, on = n && w <= room && !st.bar_titled;
    widget_t *row = P(st.bar_slot, W_PARENT);
    if (!label) {
        if (!on) return;
        label = label_create(P(row, W_PARENT), at, I(row, W_Y), w, I(row, W_H));
        if (!label) return;
        widget_set_name(label, "label_rate");
        /* every state, as tools/ipod.py gives EQ's: a bar label may draw in another */
        widget_set_prop_int(label, "style:normal:text_color", (int)0xffffffffu);
        widget_set_prop_int(label, "style:focused:text_color", (int)0xffffffffu);
        widget_set_prop_int(label, "style:disable:text_color", (int)0xffffffffu);
    }
    if (aligned != (left ? 1 : 2)) { /* towards the play state, or towards the icons */
        aligned = left ? 1 : 2;
        widget_set_prop_str(label, "style:normal:text_align_h", left ? "left" : "right");
        widget_set_prop_str(label, "style:focused:text_align_h", left ? "left" : "right");
        widget_set_prop_str(label, "style:disable:text_align_h", left ? "left" : "right");
        face_states(label, "default", SR_PX);
        widget_set_prop_int(label, "_font", -1); /* theme_font writes Classic's again */
        changed = 1;
    }
    if (on && (I(label, W_X) != at || I(label, W_W) != w || I(label, W_Y) != I(row, W_Y) ||
               I(label, W_H) != I(row, W_H))) {
        widget_move_resize(label, at, I(row, W_Y), w, I(row, W_H));
        changed = 1;
    }
    char now[16];
    if (on && (widget_get_text_utf8(label, now, sizeof now) || strcmp(now, text))) {
        widget_set_text_utf8(label, text);
        changed = 1;
    }
    int font = widget_get_prop_int(label, "_font", -1);
    if (!left) theme_font(label, ROLE_BAR, FONT_ROW); /* after the text: its face has every glyph */
    if (font != widget_get_prop_int(label, "_font", -1)) changed = 1;
    if (on != !!widget_get_visible(label)) {
        widget_set_visible(label, on, 0);
        changed = 1;
    }
    if (changed && !st.bar_timer) st.bar_timer = timer_add(bar_repaint, (void *)0, 0);
}
#endif
