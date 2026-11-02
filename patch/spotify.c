/* Spotify (docs/internals.md#spotify): librespot, built from the q2-librespot fork, runs from the
 * card's SPOT_DIR as a Spotify Connect receiver beside the stock player. It writes its state, the
 * track and position to SPOT_STATE (and the cover to SPOT_COVER), takes commands on SPOT_SOCK and
 * answers the browser on SPOT_BROWSE; this file is the other end. Like Videos' q2video, while it
 * plays it holds the standby timers and the DAC, which it sets up as stock's AirPlay receiver does,
 * and it hands the DAC over with local music both ways. Streaming's Spotify row opens the browser,
 * whose Now Playing is the page for it. */
#include "offsets.inc"
#include "peq.h"
#ifndef PEQ_HOST
#include "stock.h"
#endif

/* s into out (size bytes) with %, tab, LF and CR as %25, %09, %0A and %0D (Q2SPOT/1, docs/librespot.md);
 * its length, or -1 when it doesn't fit. */
int spot_escape(const char *s, char *out, unsigned size) {
    unsigned k = 0;
    for (; *s; ++s) {
        unsigned char c = (unsigned char)*s;
        int esc = c == '%' || c == '\t' || c == '\n' || c == '\r';
        if (k + (esc ? 3u : 1u) >= size) return -1;
        if (esc)
            out[k++] = '%', out[k++] = "0123456789ABCDEF"[c >> 4], out[k++] = "0123456789ABCDEF"[c & 15];
        else
            out[k++] = (char)c;
    }
    if (k >= size) return -1;
    out[k] = 0;
    return (int)k;
}

int hex(char c) {
    return c >= '0' && c <= '9' ? c - '0' : (c | 32) >= 'a' && (c | 32) <= 'f' ? (c | 32) - 'a' + 10 : -1;
}

/* A Q2SPOT/1 reply in buf (len bytes, a NUL after them) cut in place: f the header's 4 fields, then
 * each row's 6, unescaped. The rows' count; -1 when it is malformed (a bad escape, a NUL, a line of
 * the wrong width) or holds more than max rows. */
int spot_reply(char *buf, unsigned len, char **f, int max) {
    char *s = buf + 9, *end = buf + len;
    int k = 0;
    if (len < 9 || memcmp(buf, "Q2SPOT/1\n", 9) || strlen(buf) != len) return -1;
    while (s < end) {
        int width = k ? 6 : 4, got = 0;
        if (k + width > 4 + 6 * max) return -1;
        for (char c = 0; c != '\n';) {
            char *o = s;
            if (got == width) return -1;
            f[k + got++] = s;
            for (; s < end && *s != '\t' && *s != '\n'; ++o) {
                int v = *s != '%' ? (unsigned char)*s : s + 2 < end && hex(s[1]) >= 0 && hex(s[2]) >= 0
                                                          ? hex(s[1]) << 4 | hex(s[2])
                                                          : 0;
                if (!v) return -1; /* a NUL would cut the field */
                s += *s == '%' ? 3 : 1; /* before o, which may be s, is written */
                *o = (char)v;
            }
            c = s < end ? *s++ : '\n';
            *o = 0;
        }
        if (got != width) return -1;
        k += width;
    }
    return k ? (k - 4) / 6 : -1;
}

#ifndef PEQ_HOST

#define SPOT_BIN SPOT_DIR "/librespot"
#define SPOT_RUN "/bin/sh " SPOT_DIR "/run" /* backgrounds itself, once per boot */
#define SPOT_STATE "/tmp/q2-librespot.state" /* the fork's --status-file */
#define SPOT_COVER SPOT_STATE ".jpg"
#define SPOT_SOCK "/tmp/q2-librespot.sock" /* its --control-socket */
#define SPOT_SINK "/tmp/q2sink.sock"       /* q2video -s, its sink off the DAC: video.c SINK_SOCK */
#define SPOT_ART "/tmp/q2spot.jpg"         /* the cover at the page's size */
#define SPOT_POLL_MS 500
#define SPOT_YIELD_MS 1500 /* local music waits at most this long for librespot to let the DAC go  \
                            */
#define SPOT_STEP_MS 5000  /* a scrub tick, as Now Playing's */
#define SPOT_IDLE_MS 30000 /* not playing this long after it has played, the browser shut: librespot is stopped */
#define SPOT_CAST_MS 150000 /* as SPOT_IDLE_MS before it has played: time to cast from the phone */
#define SPOT_BROWSE "/tmp/q2-librespot.browse.sock" /* its --browse-socket, Q2SPOT/1 */
#define SPOT_REPLY_MAX (64 << 10)
#define SPOT_REQ_MAX (8 << 10)
#define SPOT_ROWS 50
#define SPOT_DEPTH 8 /* levels Back goes through; a deeper one drops the oldest past the menu */
#define SPOT_KEEP 3  /* the top ones keep their rows; Back to an older one fetches it again */
#define SPOT_WAIT_MS 15000 /* a request's deadline */
#define SPOT_PAIR_MS 2000  /* pairing, or an old add-on: account asked again this often */
#define SPOT_OLD_MS 3000   /* no browse socket this long after the start, but a state file: an old add-on */
#define SPOT_SIGNOUT_MS 15000 /* librespot exits on signout; its launcher restarts it within this */
#define SPOT_ALIVE_MS 10000   /* while it holds the timers, librespot's socket is checked this often */
#define SPOT_LOG SPOT_DIR "/spot.log" /* librespot's stderr, which run appends to */
#define SPOT_REFUSED "error audio key" /* Spotify refused a track's key: librespot-org/librespot#1649 */

/* librespot as last read; kept for the life of demo. */
static struct {
    int launched, sock, playing, paused, local, active, played, dead; /* dead: its socket refused */
    unsigned polled, idle_at, duration, position, cover, launch_at, alive_at;
    unsigned long long at;
    char raw[1536]; /* the file as last read, behind a '\n' so every key follows one */
    char state[12], track[64], title[256], artist[256], album[256];
    char out[24]; /* SPOT_OUT as last written */
    int auth, loading, old; /* auth: 0 unknown, 1 authenticated, -1 pairing; loading: 1 waiting, 2 timeout,
                             * 3 key refused */
    unsigned load_at;
    long log_from; /* SPOT_LOG's bytes before this play's */
    char wanted[64], from[64]; /* the track asked for, and the one there when it was */
} sp __attribute__((section(".scratch")));

/* The page, while open. */
static struct {
    void *page, *info, *msg, *art, *bar, *glyph, *title, *artist, *album, *elapsed, *remain, *note,
        *sub;
    unsigned timer, leave, scrub_at, shown, playing, press, press_at, bar_drawn; /* bar_drawn: its state + 1 */
    int scrub, scrub_ms;
    char art_track[64], art_tried[64]; /* art_tried: the track whose cover failed, tried art_tries times */
    int art_tries;
} ui __attribute__((section(".scratch")));

unsigned long long now_ms(void) { /* shared with radio.c; not now_ns() / 1e6: no __udivdi3 here */
    int ts[2];            /* o32 timespec */
    clock_gettime(1, ts); /* CLOCK_MONOTONIC, librespot's at= clock */
    return (unsigned long long)(unsigned)ts[0] * 1000 + (unsigned)ts[1] / 1000000;
}

/* c to the datagram socket at path, *sock opened on first use. Shared with radio.c. */
void dgram_send(int *sock, const char *path, const char *c, unsigned n) {
    if (*sock <= 0) *sock = socket(1, 1, 0); /* AF_UNIX, SOCK_DGRAM (MIPS numbering) */
    struct unix_addr to = { 1, { 0 } };
    memcpy(to.path, path, strlen(path) + 1);
    sendto(*sock, c, n, 0x40, &to, sizeof to); /* MSG_DONTWAIT */
}

/* The value of key in raw into out (n bytes), "" without one. Shared with radio.c. */
void field(const char *raw, const char *key, char *out, unsigned n) {
    char k[16];
    tk_snprintf(k, sizeof k, "\n%s=", key);
    const char *v = strstr(raw, k), *e;
    unsigned len = 0;
    if (v)
        for (v += strlen(k), e = v; *e && *e != '\n' && len + 1 < n; ++e) ++len;
    while (len && ((unsigned char)v[len] & 0xc0) == 0x80) --len;
    if (len) memcpy(out, v, len);
    out[len] = 0;
}

unsigned long long number(const char *raw, const char *key) {
    char v[24];
    unsigned long long x = 0;
    field(raw, key, v, sizeof v);
    for (const char *c = v; *c >= '0' && *c <= '9'; ++c) x = x * 10 + (unsigned)(*c - '0');
    return x;
}

/* path's bytes into raw behind a '\n', as field() reads; their count, 0 for no file. Shared
 * with radio.c. */
unsigned state_read(const char *path, char *raw, unsigned size) {
    void *f = fopen(path, "rb");
    unsigned n = f ? fread(raw + 1, 1, size - 2, f) : 0;
    if (f) fclose(f);
    raw[0] = '\n';
    raw[n + 1] = 0;
    return n;
}

/* The state file into raw; 1 when it changed. Gone (librespot not up yet) reads as no session. */
static int spot_read(char *raw, unsigned size) {
    state_read(SPOT_STATE, raw, size);
    return strcmp(raw, sp.raw) != 0;
}

/* The track's position now: the last one read, moved on by the time since while playing. */
static unsigned spot_position(void) {
    unsigned p = sp.position;
    if (sp.playing) p += (unsigned)(now_ms() - sp.at);
    return p < sp.duration ? p : sp.duration;
}

/* librespot starts playing: what stock's AirPlay page (airplay_page_init, on_airplayset_onclick)
 * does for its receiver. Local music stops (hciplayer holds the PCM even paused), then dac_take
 * (books.c). Bluetooth and USB outputs are left alone: librespot's sink plays on them itself
 * (spot_output). */
static void spot_take(void) {
    headset_manual();
    radio_stop(); /* Internet Radio's q2video holds the PCM */
    if (mclGetPlayStatus() != 1) player_stop(); /* 1 stopped */
    dac_take();
}

/* The output in use, which the card's aplay.sh reads each time librespot starts its sink: SPOT_OUT
 * holds output_device's device and volume for Bluetooth or a USB DAC (q2video -s plays there), and
 * is gone for the headphone DAC (aplay on plughw:0,0), except with Mono Audio on: q2video mixes it,
 * so the DAC is plughw:0,0 with an h volume (the DAC applies it). Written aside and renamed, when it
 * changes; a running q2video gets v and the volume, as radio's does. ponytail: a new device, or
 * Mono Audio's switch on the DAC, waits for the sink's next start (pause and play), as Videos and
 * Internet Radio keep theirs till they end. */
static void spot_output(void) {
    char vol[5], out[sizeof sp.out] = "", v[2] = { 'v', (char)headset_gain() };
    const char *dev = output_device(vol);
    if (dev) tk_snprintf(out, sizeof out, "%s %s\n", dev, vol);
    else if (mono_on() || swap_on()) tk_snprintf(out, sizeof out, "plughw:0,0 h%s\n", vol);
    if (!strcmp(out, sp.out)) return;
    memcpy(sp.out, out, sizeof out);
    void *f = out[0] ? fopen(SPOT_OUT ".tmp", "w") : 0;
    if (f) fwrite(out, 1, strlen(out), f), fclose(f), rename(SPOT_OUT ".tmp", SPOT_OUT);
    if (!out[0]) unlink(SPOT_OUT);
    dgram_send(&sp.sock, SPOT_SINK, v, 2);
}

static int spot_started(void);

static void spot_parse(void) {
    field(sp.raw, "state", sp.state, sizeof sp.state);
    field(sp.raw, "track", sp.track, sizeof sp.track);
    field(sp.raw, "title", sp.title, sizeof sp.title);
    field(sp.raw, "artist", sp.artist, sizeof sp.artist);
    field(sp.raw, "album", sp.album, sizeof sp.album);
    sp.duration = (unsigned)number(sp.raw, "duration");
    sp.position = (unsigned)number(sp.raw, "position");
    sp.at = number(sp.raw, "at");
    sp.cover = (unsigned)number(sp.raw, "cover");
    if (sp.auth > 0 && (!sp.state[0] || !strcmp(sp.state, "none"))) sp.auth = 0;
    if (sp.track[0] && (!strcmp(sp.state, "playing") || !strcmp(sp.state, "paused"))) sp.auth = 1;
    if (sp.loading && spot_started()) sp.loading = 0;
}

/* The play asked for has a track: the one asked for, or any other than before (a relinked id), or
 * anything at all once the wait timed out. */
static int spot_started(void) {
    return (!strcmp(sp.state, "playing") || !strcmp(sp.state, "paused")) && sp.track[0] &&
           (sp.loading == 2 || !strcmp(sp.track, sp.wanted) || strcmp(sp.track, sp.from));
}

static void spot_launch(void) {
    if (sp.launched || access(SPOT_BIN, 0)) return;
    sp.launched = 1;
    sp.idle_at = sp.launch_at = time_now_ms();
    system(SPOT_RUN);
}

static void scrub_commit(void) {
    char c[16];
    int n = tk_snprintf(c, sizeof c, "S%d", ui.scrub_ms);
    ui.scrub = 0;
    dgram_send(&sp.sock, SPOT_SOCK, c, (unsigned)n);
}

static void spot_refresh(void);
static void *browsing(void);
static void spot_restarting(void);

/* SPOT_LOG's length, 0 without one. */
static long log_size(void) {
    void *f = fopen(SPOT_LOG, "rb");
    long n = f && !fseek(f, 0, 2) ? ftell(f) : 0;
    if (f) fclose(f);
    return n;
}

/* While a play loads: 1 once librespot has logged SPOT_REFUSED since sp.log_from, which moves on
 * (less a match's length, for a line cut between two reads). Only the last 4 KB new are read. */
static int spot_refused(void) {
    char buf[4096];
    void *f = fopen(SPOT_LOG, "rb");
    long end = f && !fseek(f, 0, 2) ? ftell(f) : 0, from = sp.log_from;
    if (end < from) from = 0; /* a new log */
    if (end - from > (long)sizeof buf - 1) from = end - (long)sizeof buf + 1;
    unsigned n = f && end > from && !fseek(f, from, 0) ? fread(buf, 1, (unsigned)(end - from), f) : 0;
    if (f) fclose(f);
    buf[n] = 0;
    long keep = end - (long)sizeof SPOT_REFUSED + 2;
    if (keep > sp.log_from) sp.log_from = keep;
    return strstr(buf, SPOT_REFUSED) != 0;
}

/* SPOT_IDLE_MS after playback ended with the browser shut, or Streaming's Stop Streaming Services
 * row: librespot and its restart loop killed, as Home's Rockbox row does, and the keys local
 * music's. Streaming's Spotify row starts it again. */
void spot_stop(void) {
    int sock = sp.sock;
    system(SPOT_KILL);
    memset(&sp, 0, sizeof sp);
    sp.sock = sock;
}

/* librespot's control socket takes a connection (nothing sent): a crashed one leaves its state
 * and download files saying playing, which would hold the timers and the DAC for good. */
static int spot_alive(void) {
    struct unix_addr to = { 1, SPOT_SOCK };
    int fd = socket(1, 1, 0), ok = fd < 0 || !connect(fd, &to, sizeof to); /* AF_UNIX, SOCK_DGRAM */
    if (fd >= 0) close(fd);
    return ok;
}

/* ringnav_sleep, every UI loop pass: nothing until Streaming's Spotify row started librespot; then,
 * paced to SPOT_POLL_MS, the output (spot_output), its state, and while it plays the standby and
 * auto-power-off timers held and the DAC kept on (check_dacoff_state). The screen's own timer runs,
 * so the screen still turns off. */
void spot_poll(void) {
    if (!sp.launched) return;
    unsigned now = time_now_ms();
    if (now - sp.polled < SPOT_POLL_MS) return;
    sp.polled = now;
    spot_output();
    char raw[sizeof sp.raw];
    if (spot_read(raw, sizeof raw)) {
        memcpy(sp.raw, raw, sizeof raw);
        spot_parse();
        sp.dead = 0;
    }
    state_read(SPOT_DOWNLOAD, raw, sizeof raw);
    int downloading = number(raw, "downloading") == 1;
    int playing = !strcmp(sp.state, "playing");
    if ((playing || downloading) && now - sp.alive_at >= SPOT_ALIVE_MS)
        sp.alive_at = now, sp.dead = !spot_alive();
    if (sp.dead) playing = downloading = 0; /* a crash's last word: no hold, so it idles out */
    /* Refused, librespot skips on to the next track and the next, through the whole list, and no
     * pause stops it while it loads; killed, run starts it again with no track. */
    if (sp.loading == 1 && spot_refused()) {
        sp.loading = 3;
        system("killall -9 librespot aplay");
        spot_restarting();
    }
    if (playing && !sp.playing) {
        sp.playing = 1;
        spot_take();
    }
    sp.playing = playing;
    sp.paused = !strcmp(sp.state, "paused");
    if (playing || downloading)
        reset_poweroptions_timer(1, 1, 0); /* standby, auto power-off; not the screen's */
    if (playing) {
        if (I(g_dacoff_time, 0) > 0) I(g_dacoff_time, 0) = 0;
        sp.local = 0;
    } else if (mclGetPlayStatus() == 2)
        sp.local = 1; /* local music plays: the keys are its again */
    sp.active = (playing || sp.paused) && !sp.local;
    if (playing || downloading || (browsing() && g_backlight_status))
        sp.played |= playing, sp.idle_at = now;
    else if (now - sp.idle_at >= (sp.played ? SPOT_IDLE_MS : SPOT_CAST_MS)) {
        if (browsing()) window_close(browsing()); /* left open dark: shut, as Back would */
        spot_stop();
    }
    if (ui.scrub && now - ui.scrub_at >= SCRUB_MS) scrub_commit();
}

/* mclStartPlayer's hook (navigation.c): local music is about to open the PCM. A playing librespot
 * is paused first and given up to SPOT_YIELD_MS to say so, which it does once its aplay has ended
 * (the fork's sink stops before the Paused event). May run on the player's thread: no widgets. */
void spot_yield(void) {
    if (!sp.playing) return;
    sp.local = 1;
    sp.active = 0;
    dgram_send(&sp.sock, SPOT_SOCK, "s", 1);
    char raw[sizeof sp.raw], state[12] = "playing";
    unsigned start = time_now_ms();
    while (!strcmp(state, "playing") && time_now_ms() - start < SPOT_YIELD_MS) {
        sleep_ms(20);
        spot_read(raw, sizeof raw);
        field(raw, "state", state, sizeof state);
    }
    sp.playing = !strcmp(state, "playing"); /* the next track's start need not ask again */
}

/* ringnav(), any page, after stock's key-lock filter: while Spotify is the last thing played,
 * Play/Pause and the side buttons are its. */
int spot_media(unsigned key) {
    const char *c = key == KEY_PLAY       ? "p"
                    : key == KEY_FWD_BTN  ? "n"
                    : key == KEY_BACK_BTN ? "b"
                                          : 0;
    if (!c || !sp.active) return 0;
    headset_manual();
    dgram_send(&sp.sock, SPOT_SOCK, c, 1);
    return 1;
}

/* A single centre press, DOUBLE_CLICK_MS on, never while scrubbing: it replays the release to stock
 * on_wm_keyup_fun, which turns the screen off, as Now Playing's np_single. */
static int spot_single(const void *info) {
    static const unsigned release[EVENT_KEY / 4 + 1] = { [EVENT_KEY / 4] = KEY_CENTER };
    ui.press = 0;
    if (g_backlight_status) on_wm_keyup_fun((void *)0, (void *)release);
    return 0;
}

/* ringnav(), the page on top, as Now Playing: a centre press waits DOUBLE_CLICK_MS (center_press); a second one
 * starts a scrub, else it turns the screen off. While scrubbing one press seeks it at once, the wheel moves
 * it SPOT_STEP_MS a tick, and SCRUB_MS without a tick seeks there (spot_poll). Otherwise the wheel
 * stays stock's volume. */
int spot_key(void *top, unsigned key) {
    if (!ui.page || top != ui.page || sp.loading || sp.auth < 0 || !sp.track[0] || !sp.duration) return 0;
    if (key == KEY_CENTER) {
        int press = center_press(&ui.press, &ui.press_at, spot_single, ui.scrub);
        if (press != 2) return press;
        if (ui.scrub)
            scrub_commit();
        else
            ui.scrub = 1, ui.scrub_ms = (int)spot_position();
    } else if (ui.scrub && (key == KEY_NEXT || key == KEY_PREV)) {
        int ms = ui.scrub_ms + (key == KEY_NEXT ? SPOT_STEP_MS : -SPOT_STEP_MS);
        ui.scrub_ms = ms<0 ? 0 : ms>(int) sp.duration ? (int)sp.duration : ms;
    } else
        return 0;
    ui.scrub_at = time_now_ms();
    spot_refresh();
    return 1;
}

static void times(unsigned ms) {
    char t[24];
    toolsTimeItoa(t, (int)(ms / 1000));
    widget_set_text_utf8(ui.elapsed, t);
    t[0] = '-'; /* Now Playing's remaining time: whole seconds less whole seconds */
    toolsTimeItoa(t + 1, (int)(sp.duration / 1000 - ms / 1000));
    widget_set_text_utf8(ui.remain, t);
}

/* The page from sp: the track, or why there is none. Labels are written only when they change. */
static void spot_refresh(void) {
    if (sp.loading == 1 && time_now_ms() - sp.load_at >= SPOT_WAIT_MS) sp.loading = 2;
    if (sp.loading == 2 && spot_started()) sp.loading = 0; /* a paused track writes no new state */
    int track = sp.auth >= 0 && !sp.loading && sp.track[0] && strcmp(sp.state, "none"), installed = sp.launched;
    widget_set_visible(ui.info, track, 0);
    widget_set_visible(ui.msg, !track, 0);
    if (!track) {
        const char *note = "Loading…", *sub = "Waiting for Spotify";
        if (!installed) note = "Spotify isn't on the card", sub = "See the Q2 Pod guide to add it";
        else if (sp.auth < 0) note = "Open Spotify on your phone", sub = "and choose Q2";
        else if (sp.loading == 3) note = "Spotify refused this account", sub = "Its tracks can't play on the Q2";
        else if (sp.loading == 2) note = "Playback didn't start", sub = "Go back and try again";
        else if (sp.loading) sub = "Starting playback";
        else if (sp.old) note = "Update Spotify on the card", sub = "See the Q2 Pod guide";
        else if (sp.auth > 0) note = "Nothing playing", sub = "Choose music in Spotify";
        widget_set_text_utf8(ui.note, note);
        widget_set_text_utf8(ui.sub, sub);
        return;
    }
    void *fields[] = { ui.title, ui.artist, ui.album };
    const char *values[] = { sp.title, sp.artist, sp.album };
    for (unsigned i = 0; i < 3; ++i)
        if (text_differs(fields[i], values[i])) {
            widget_set_text_utf8(fields[i], values[i]);
            widget_set_prop_int(fields[i], "xoffset", 0);
        }
    /* ponytail: the cover is sized on the UI thread, one decode a track; a worker if it stutters */
    /* A cover that fails (written after its state line, or bad) is tried again on the next 3 ticks. */
    if (tk_strcmp(ui.art_tried, sp.track)) ui.art_tries = 0;
    if (sp.cover && tk_strcmp(ui.art_track, sp.track) && ui.art_tries < 4) {
        if (thumb(SPOT_COVER, SPOT_ART, SPOT_ART_PX, SPOT_ART_PX) &&
            image_show(ui.art, "file://" SPOT_ART, 0))
            tk_snprintf(ui.art_track, sizeof ui.art_track, "%s", sp.track);
        else {
            tk_snprintf(ui.art_tried, sizeof ui.art_tried, "%s", sp.track);
            ui.art_tries++;
            image_base_set_image(ui.art, PLACEHOLDER);
        }
    } else if (!sp.cover && ui.art_track[0]) {
        ui.art_track[0] = 0;
        image_base_set_image(ui.art, PLACEHOLDER);
    }
    unsigned ms = ui.scrub ? (unsigned)ui.scrub_ms : spot_position();
    if (ms / 1000 + 1 != ui.shown) {
        ui.shown = ms / 1000 + 1;
        times(ms);
    }
    /* only when the drawn width (spot_paint's) or the scrub's colour moves: this runs every tick */
    unsigned bar = (unsigned)((ms >> 4) * SPOT_BAR_W / ((sp.duration >> 4) | 1)) << 1 | (ui.scrub != 0);
    if (bar + 1 != ui.bar_drawn) {
        ui.bar_drawn = bar + 1;
        widget_invalidate_force(ui.bar, 0);
    }
    if (ui.playing != (unsigned)sp.playing + 1) {
        ui.playing = (unsigned)sp.playing + 1;
        widget_invalidate_force(ui.glyph, 0);
    }
}

static int tick(const void *unused) {
    if (g_backlight_status) spot_refresh();
    return 8; /* RET_REPEAT */
}

static void box(void *canvas, int x, int y, int w, int h, unsigned color, unsigned radius) {
    int r[4] = { x, y, w, h };
    if (canvas_fill_rounded_rect(canvas, r, (void *)0, &color, radius)) {
        canvas_set_fill_color(canvas, color);
        canvas_fill_rect(canvas, x, y, w, h);
    }
}

/* ringnav_paint, every widget after stock painted it: the bar, as Now Playing's capsule (the
 * accent's light tone), and the play state in the top row. */
void spot_paint(void *w, void *canvas) {
    if (!ui.page || !w || !P(canvas, CANVAS_LCD) || (w != ui.bar && w != ui.glyph)) return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    unsigned tone = rgba(accent_tone(2), 255);
    if (w == ui.bar) {
        unsigned ms = ui.scrub ? (unsigned)ui.scrub_ms : spot_position();
        int x = (int)((ms >> 4) * SPOT_BAR_W / ((sp.duration >> 4) | 1)); /* 32-bit to 57 h */
        int h = np_bar_h();
        box(canvas, 0, 0, SPOT_BAR_W, h, rgba(TRACK_COLOR, 255), h / 2);
        if (x) box(canvas, 0, 0, x < h ? h : x, h, ui.scrub ? 0xffffffffu : tone, h / 2);
    } else { /* a triangle playing, two bars otherwise */
        canvas_set_fill_color(canvas, 0xffffffffu);
        if (sp.playing)
            for (int i = 0; i < 7; ++i) canvas_fill_rect(canvas, 2 * i, i, 2, 14 - 2 * i);
        else
            canvas_fill_rect(canvas, 0, 0, 4, 14), canvas_fill_rect(canvas, 8, 0, 4, 14);
    }
    stock_canvas_set_fill_color(canvas, fill);
}

static int leave(const void *unused) {
    ui.leave = 0;
    navigator_back();
    return 0;
}

/* Return: a scrub seeks first, as on Now Playing; then back to the browser. */
static int keyup(void *ctx, void *event) {
    if (I(event, EVENT_KEY) != KEY_RETURN) return 0;
    if (ui.scrub)
        scrub_commit();
    else
        rearm(&ui.leave, leave, 0);
    return 11; /* RET_STOP */
}

static int closed(void *ctx, void *event) {
    stop_timer(&ui.timer);
    stop_timer(&ui.leave);
    stop_timer(&ui.press);
    if (ui.scrub) scrub_commit();
    memset(&ui, 0, sizeof ui);
    return 0;
}

void *label(void *parent, int x, int y, int w, int h, const char *style, int px,
                   unsigned color) {
    void *l = text(parent, x, y, w, h);
    widget_use_style(l, style);
    widget_set_prop_int(l, "style:normal:font_size", px);
    widget_set_prop_int(l, "style:normal:text_color", (int)color);
    return l;
}

/* The Now Playing page: the browser's row, a track it started, or without librespot on the card. */
static void spot_np(void) {
    if (ui.page || !(ui.page = page_open("spotify_page", closed, keyup))) return;
    void *f = widget_factory(), *page = ui.page;
    int h = widget_get_prop_int(page, "h", 290);
    ui.info = widget_factory_create_widget(f, "view", page, 0, 0, 375, h);
    ui.msg = widget_factory_create_widget(f, "view", page, 0, 0, 375, h);
    widget_set_text_utf8(label(ui.info, 16, 12, 200, 16, "s_scrlabel_white20l", 16, SPOT_GREY),
                         "Spotify");
    ui.glyph = widget_factory_create_widget(f, "view", ui.info, 337, 13, 14, 14); /* NP's top row */
    ui.art = image_create(ui.info, SPOT_ART_X, SPOT_ART_Y, SPOT_ART_PX, SPOT_ART_PX);
    image_set_draw_type(ui.art, 4); /* as Coverflow's covers */
    image_base_set_image(ui.art, PLACEHOLDER);
    ui.title =
        label(ui.info, SPOT_TEXT_X, SPOT_ART_Y + 41, SPOT_TEXT_W, 28, "s_scrlabel_white20l", NP_TITLE_PX, 0xffffffffu);
    ui.artist =
        label(ui.info, SPOT_TEXT_X, SPOT_ART_Y + 75, SPOT_TEXT_W, 22, "s_scrlabel_white20l", NP_META_PX,
              0xff000000u | NP_ARTIST_RGB);
    ui.album =
        label(ui.info, SPOT_TEXT_X, SPOT_ART_Y + 103, SPOT_TEXT_W, 22, "s_scrlabel_white20l", NP_META_PX,
              0xff000000u | NP_ALBUM_RGB);
    void *fields[] = { ui.title, ui.artist, ui.album };
    for (unsigned i = 0; i < 3; ++i) {
        hscroll_label_set_only_focus(fields[i], 0);
        hscroll_label_set_ellipses(fields[i], 0);
    }
    ui.bar = widget_factory_create_widget(f, "view", ui.info, SPOT_BAR_X, SPOT_BAR_Y, SPOT_BAR_W,
                                          SPOT_BAR_H);
    ui.elapsed =
        label(ui.info, SPOT_TIME_X, SPOT_TIMES_Y, 80, 16, "s_scrlabel_white20l", 14, SPOT_GREY);
    ui.remain = label(ui.info, 375 - SPOT_TIME_X - 80, SPOT_TIMES_Y, 80, 16, "s_scrlabel_white20r",
                      14, SPOT_GREY);
    ui.note =
        label(ui.msg, CF_EDGE, 100, 375 - 2 * CF_EDGE, 30, "s_scrlabel_white20c", 20, 0xffffffffu);
    ui.sub =
        label(ui.msg, CF_EDGE, 134, 375 - 2 * CF_EDGE, 24, "s_scrlabel_white20c", 16, SPOT_GREY);
    ui.timer = timer_add(tick, 0, 250);
    spot_refresh();
}

/* The browser (docs/internals.md#spotify): spotbrowse_page, one page_list rebuilt per level, each
 * level a Q2SPOT/1 request to librespot's SPOT_BROWSE and its reply, cut in place. lv[0] is the
 * base: "account" asked first, then the menu, the pairing prompt or an old add-on's rows. */
enum { L_BASE, L_LIST, L_CATS, L_CONFIRM, L_DOWNLOADS, L_JOB, L_STORAGE, L_SELECT, L_REMOVE };
enum { S_LOAD, S_READY, S_ERROR, S_PAIR, S_OLD, S_WAIT }; /* S_WAIT: no socket yet, asked again */
enum { A_FETCH, A_PLAY, A_ENQUEUE, A_SET, A_SIGNOUT, A_DOWNLOAD, A_CAPS, A_REMOVE, A_PREVIEW };
enum { M_PLAYING, M_LIKED, M_PLAYLISTS, M_ALBUMS, M_ARTISTS, M_SEARCH, M_QUEUE, M_SETTINGS, M_ACCOUNT, M_DOWNLOADED, M_DOWNLOADS };
enum { G_ROW, G_SHOW, G_SEARCH };

typedef struct {
    int kind, state, n, sel, retry;
    unsigned id; /* the fetch whose reply this level takes */
    char *buf;   /* the reply; 0 before it came, or once evicted */
    char *f[4 + 6 * SPOT_ROWS];
    char title[256], note[128], op[16], args[320], cursor[1100]; /* args, cursor: "\t" and escaped */
} level_t;

/* The request in flight, on its pthread: written from buf, the reply then read into it. */
static struct {
    unsigned long thread;
    int running, fail; /* fail: W_* */
    volatile int cancel, done;
    unsigned id, n, len; /* id: the latest request's, never reused */
    char *buf;
} wk __attribute__((section(".scratch")));
enum { W_OK, W_NOSOCK, W_FAIL };

/* The page, while open. act: what the request in flight is for (A_*). */
static struct {
    void *page, *view, *edit;
    unsigned timer, go, leave, poll_at, until; /* until: no socket before it is librespot starting */
    int depth, act, next, arg, playing, rows[11], caps_checked, downloads, can_download;
    char selected[6500], remove_kind[16], remove_note[128];
    char term[64];
    level_t *lv; /* SPOT_DEPTH of them, calloc'd */
} br __attribute__((section(".scratch")));

static void *browsing(void) { return br.page; }
/* librespot killed: no socket for a while is it starting again, as after a sign-out. */
static void spot_restarting(void) { br.until = time_now_ms() + SPOT_SIGNOUT_MS; }

static level_t *top(void) { return &br.lv[br.depth - 1]; }
static const char *cell(const level_t *l, int i, int k) { return l->f[4 + 6 * i + k]; }
static int has_track(void) { return sp.track[0] && strcmp(sp.state, "none"); }
static const char *const MENU[] = { "Now Playing", "Liked Songs", "Playlists", "Albums", "Artists",
                                    "Search", "Queue", "Playback Settings", "Account", "Downloaded Tracks", "Downloads" };
static const char *const CATS[] = { "Tracks", "Albums", "Artists", "Playlists" };

/* "\t" and s escaped appended to out (size bytes); 0 when it doesn't fit. */
static int put(char *out, unsigned size, const char *s) {
    unsigned k = strlen(out);
    return k + 1 < size && (out[k] = '\t', spot_escape(s, out + k + 1, size - k - 1) >= 0);
}

static void request(const char *op, const char *tail, int act);

static void remove_request(int confirm) {
    char tail[7000] = "";
    if (put(tail, sizeof tail, br.remove_kind) && put(tail, sizeof tail, br.selected) &&
        (!confirm || put(tail, sizeof tail, "confirm")))
        request("remove", tail, confirm ? A_REMOVE : A_PREVIEW);
}

static void fetch_cancel(void) {
    unsigned none = 0;
    worker_stop(wk.thread, &wk.running, &wk.cancel, &none);
    free(wk.buf);
    wk.buf = 0;
    wk.done = 0;
}

/* shutdown(fd, SHUT_WR): demo imports no shutdown(), so the o32 syscall itself. */
__asm__(".globl spot_shut\nspot_shut:\nli $a1, 1\nli $v0, 4182\nsyscall\njr $ra\n");
void spot_shut(int fd);

/* One connection a request: written, shut for writing, and read to EOF within SPOT_WAIT_MS, 100 ms
 * at a time so a cancel is joined at once. */
static void *worker(void *unused) {
    struct unix_addr to = { 1, SPOT_BROWSE };
    unsigned long long end = now_ms() + SPOT_WAIT_MS;
    unsigned sent = 0;
    int fd = socket(1, 2, 0), k = 1; /* AF_UNIX, SOCK_STREAM (MIPS numbering) */
    wk.fail = W_NOSOCK;
    if (fd >= 0 && fd < 1024 && !fcntl(fd, 4, 0x80) && !connect(fd, &to, sizeof to)) { /* F_SETFL, O_NONBLOCK (MIPS) */
        wk.fail = W_FAIL;
        while (!wk.cancel && now_ms() < end) {
            unsigned set[32] = { 0 }; /* fd_set */
            int tv[2] = { 0, 100000 };
            set[fd / 32] = 1u << fd % 32;
            int writing = sent < wk.len;
            if ((k = select(fd + 1, writing ? 0 : set, writing ? set : 0, 0, tv)) <= 0) {
                if (k) break; /* not a timeout */
                continue;
            }
            if (writing) {
                k = send(fd, wk.buf + sent, wk.len - sent, 0x4000); /* MSG_NOSIGNAL */
                if (k <= 0) break;
                if ((sent += (unsigned)k) == wk.len) spot_shut(fd);
                continue;
            }
            if ((k = recv(fd, wk.buf + wk.n, SPOT_REPLY_MAX + 1 - wk.n, 0)) <= 0 ||
                (wk.n += (unsigned)k) > SPOT_REPLY_MAX) {
                wk.fail = k ? W_FAIL : W_OK;
                break;
            }
        }
    }
    if (fd >= 0) close(fd);
    if (wk.n <= SPOT_REPLY_MAX) wk.buf[wk.n] = 0; /* past the reply, the request's bytes */
    wk.done = 1;
    return 0;
}

/* op and tail (its arguments: "\t" and escaped each) asked on wk's pthread, for act or (A_FETCH)
 * the top level. The one before is dropped: one request at a time. */
static void request(const char *op, const char *tail, int act) {
    fetch_cancel();
    br.act = act;
    if (act == A_PLAY) {
        const char *uri = strrchr(tail, '\t');
        tk_snprintf(sp.wanted, sizeof sp.wanted, "%s", uri && strstr(uri + 1, "spotify:track:") == uri + 1 ? uri + 15 : "");
        tk_snprintf(sp.from, sizeof sp.from, "%s", sp.track);
    }
    ++wk.id;
    if (act == A_FETCH) top()->id = wk.id;
    wk.cancel = 0;
    wk.n = 0;
    wk.fail = W_FAIL;
    int n = (wk.buf = calloc(SPOT_REPLY_MAX + 2, 1))
                ? tk_snprintf(wk.buf, SPOT_REQ_MAX, "Q2SPOT/1\n%u\t%s%s\n", wk.id, op, tail)
                : 0;
    wk.len = n > 0 && n < SPOT_REQ_MAX ? (unsigned)n : 0;
    wk.running = wk.len && !pthread_create(&wk.thread, 0, worker, 0);
    wk.done = !wk.running; /* the tick reports it */
}

static void fetch(level_t *l) {
    char tail[sizeof l->args + sizeof l->cursor];
    tk_snprintf(tail, sizeof tail, "%s%s", l->args, l->cursor);
    request(l->op, tail, A_FETCH);
}

static int clicked(void *ctx, void *event);

/* A list's row after its items: More…, or Sign out. */
static int more(const level_t *l) { return l->f[3][0] || !strcmp(l->op, "account"); }

static void *row(int i, const char *caption, const char *detail) {
    return page_row_detail(br.view, i, caption, detail, clicked);
}

/* A list row's kind that opens a level (go()), drawn with a chevron. */
static int opens(const char *kind) {
    return !strcmp(kind, "album") || !strcmp(kind, "playlist") || !strcmp(kind, "artist") ||
           !strcmp(kind, "collection") || !strcmp(kind, "job");
}

/* A row that opens a list, with its chevron. */
static void *into(int i, const char *caption, const char *detail) {
    return page_row_detail_into(br.view, i, caption, detail, clicked);
}

/* The top level: its rows, or "Loading…", its error, the pairing prompt or the menu. */
static void show(void) {
    level_t *l = top();
    int n = 0;
    br.view = br.edit = 0;
    if (l->state == S_LOAD && !((wk.running || wk.done) && br.act == A_FETCH && wk.id == l->id))
        fetch(l);
    if (l->state == S_LOAD || l->state == S_WAIT) {
        page_list(br.page, br.page, 0, "Loading…", 0, 48);
        return;
    }
    if (l->state == S_ERROR) {
        br.view = page_list(br.page, br.page, 0, l->note, n = l->retry, 48);
        if (n) row(0, "Retry", 0);
    } else if (l->state == S_PAIR) {
        br.view = page_list(br.page, br.page, 0, "Spotify", n = 1, 64);
        row(0, "Open Spotify on your phone and choose Q2", "Both on the same Wi-Fi");
    } else if (l->state == S_OLD) {
        br.view = page_list(br.page, br.page, 0, "Spotify", n = 2, 64);
        row(0, "Now Playing", "");
        row(1, "Update Spotify on the card", "to browse on the Q2");
    } else if (l->kind == L_BASE) {
        br.playing = has_track();
        for (int r = M_PLAYING; r <= M_DOWNLOADS; ++r)
            if ((r != M_DOWNLOADED || !br.downloads) && ((r != M_PLAYING && r != M_QUEUE && r != M_SETTINGS) || br.playing)) br.rows[n++] = r;
        br.view = page_list(br.page, br.page, 0, "Spotify", n, 48);
        for (int i = 0; i < n; ++i) /* Now Playing and Search open no list */
            if (br.rows[i] == M_PLAYING || br.rows[i] == M_SEARCH)
                row(i, MENU[br.rows[i]], 0);
            else
                into(i, MENU[br.rows[i]], 0);
    } else if (l->kind == L_CATS) {
        br.view = page_list(br.page, br.page, 0, l->title, n = 4, 48);
        for (int i = 0; i < n; ++i) into(i, CATS[i], 0);
    } else if (l->kind == L_DOWNLOADS) {
        br.view = page_list(br.page, br.page, 0, "Downloads", n = br.downloads ? 4 : 2 + br.can_download, 64);
        if (br.downloads) {
            into(0, "Downloaded Playlists", "");
            into(1, "Downloaded Tracks", "");
            into(2, "Download Progress", "");
            into(3, "Storage Management", "");
        } else {
            into(0, "Downloaded Tracks", "");
            if (br.can_download) row(1, "Download Status", "The latest download's progress"); /* older add-ons' */
            row(n - 1, "Update Spotify on the card", "for saved playlists and download controls");
        }
    } else if (l->kind == L_STORAGE) {
        br.view = page_list(br.page, br.page, 0, "Storage Management", n = 3, 64);
        into(0, "Remove Playlists…", "");
        into(1, "Remove Tracks…", "");
        row(2, "Clear All Downloads…", "Keeps login and settings");
    } else if (l->kind == L_REMOVE) {
        br.view = page_list(br.page, br.page, 0, "Remove Downloads?", n = 3, 64);
        row(0, br.remove_note, "Track removal can make playlists incomplete");
        row(1, "Cancel", "");
        row(2, "Remove", "");
    } else if (l->kind == L_JOB) {
        static const char *const actions[] = { "Pause", "Resume", "Cancel Download", "Retry Failed", "Update Download" };
        br.view = page_list(br.page, br.page, 0, l->title, n = 5, 48);
        for (int i = 0; i < n; ++i) row(i, actions[i], 0);
    } else if (l->kind == L_CONFIRM) {
        br.view = page_list(br.page, br.page, 0, "Sign Out of Spotify?", n = 2, 48);
        row(0, "Cancel", 0);
        row(1, "Sign Out", 0);
    } else {
        int download = (br.can_download && !strcmp(l->op, "playlist")) || (br.downloads && !strcmp(l->op, "liked"));
        int manage = br.downloads && !strcmp(l->op, "collection");
        if (!(n = l->n + more(l) + download + manage + (l->kind == L_SELECT))) {
            page_list(br.page, br.page, 0, "Nothing here", 0, 48);
            return;
        }
        br.view = page_list(br.page, br.page, 0, l->title, n, 64);
        for (int i = 0; i < l->n; ++i) {
            const char *v = cell(l, i, 2), *kind = cell(l, i, 0);
            if (l->kind == L_SELECT)
                row(i, v, csv_find(br.selected, cell(l, i, 1)) ? "Selected" : "Press to select");
            else if (strcmp(kind, "setting"))
                (opens(kind) ? into : row)(i, v, cell(l, i, 3));
            else if (strcmp(cell(l, i, 1), "shuffle"))
                row(i, "Repeat", !strcmp(v, "context") ? "All" : !strcmp(v, "track") ? "One" : "Off");
            else /* the switch, as Shanling's settings */
                list_toggle(widget_get_child(row(i, "", ""), 0), "Shuffle", !strcmp(v, "on"));
        }
        if (more(l)) row(l->n, l->f[3][0] ? "More…" : "Sign Out", "");
        if (download) row(n - 1, !strcmp(l->op, "liked") ? "Download Liked Songs" : "Download Playlist", "For offline listening");
        if (manage) into(n - 1, "Download Controls", "Update, retry, pause, resume or cancel");
        if (l->kind == L_SELECT) row(n - 1, "Remove Selected…", "Confirm the affected count");
    }
    if (n) ringnav_select(br.view, l->sel < n ? l->sel : n - 1, n);
}

static void drop(level_t *l) {
    free(l->buf);
    memset(l, 0, sizeof *l);
}

/* The base alone, in state; the pairing prompt and an old add-on's rows ask again from the tick. */
static void base(int state) {
    br.poll_at = time_now_ms() + SPOT_PAIR_MS;
    if (br.depth == 1 && top()->state == state) return; /* as shown */
    while (br.depth > 1) drop(&br.lv[--br.depth]);
    top()->state = state;
    show();
}

/* A level over the top one, which keeps its selected row; the one SPOT_KEEP down lets its rows go. */
static void push(int kind, const char *op, const char *args, const char *cursor, const char *title) {
    level_t *l;
    /* The arguments may point into the top level, including the slot reused at the depth limit. */
    level_t next = { 0 };
    next.kind = kind;
    next.state = (kind == L_LIST || kind == L_SELECT) ? S_LOAD : S_READY;
    tk_snprintf(next.op, sizeof next.op, "%s", op);
    tk_snprintf(next.args, sizeof next.args, "%s", args);
    tk_snprintf(next.cursor, sizeof next.cursor, "%s", cursor);
    tk_snprintf(next.title, sizeof next.title, "%s", title);
    if (br.view) top()->sel = widget_get_prop_int(br.view, "_ringnav_index", 0);
    if (br.depth == SPOT_DEPTH) { /* the oldest past the base forgotten */
        drop(&br.lv[1]);
        for (int i = 1; i < SPOT_DEPTH - 1; ++i) br.lv[i] = br.lv[i + 1];
        --br.depth;
    }
    if (br.depth >= SPOT_KEEP && ((l = &br.lv[br.depth - SPOT_KEEP])->kind == L_LIST || l->kind == L_SELECT) && l->buf) {
        free(l->buf);
        l->buf = 0;
        l->n = 0;
        l->state = S_LOAD;
    }
    l = &br.lv[br.depth++];
    *l = next;
    show();
}

/* Back a level; its fetch, if in flight, dropped. */
static void pop(void) {
    fetch_cancel();
    br.act = A_FETCH;
    drop(top());
    --br.depth;
    show();
}

/* The finished request: an action's outcome, or the top level's rows, error or pairing prompt. A
 * reply without the request's id, or for a level since left, is dropped. */
static void got(void) {
    level_t *l = top();
    /* A shown level's refresh (Download Progress) is cut into head too, so a failed one leaves its
     * fields on the reply it shows. */
    char id[12], *head[4 + 6 * SPOT_ROWS], **f = br.act || l->state == S_READY ? head : l->f, *reply = wk.buf; /* ours: a request below takes wk */
    int act = br.act, n = wk.fail == W_OK ? spot_reply(reply, wk.n, f, act && act != A_CAPS ? 0 : SPOT_ROWS) : -1;
    wk.buf = 0;
    tk_snprintf(id, sizeof id, "%u", wk.id);
    if (n >= 0 && strcmp(f[0], id)) n = -1;
    const char *status = n < 0 ? "error" : f[1];
    const char *msg = wk.fail == W_NOSOCK      ? "Spotify isn't running"
                      : n >= 0 && f[2][0] ? f[2]
                                               : "Spotify didn't answer";
    int ok = !strcmp(status, "ok"), login = !strcmp(status, "login");
    if (login || (ok && act == A_SIGNOUT)) {
        sp.auth = -1;
        sp.loading = 0;
        sp.active = sp.playing = sp.paused = 0;
        sp.track[0] = 0;
    } else if (ok) {
        sp.auth = 1;
        sp.old = 0;
    }
    br.act = A_FETCH;
    if (act == A_CAPS) {
        br.caps_checked = n >= 0 && strcmp(status, "offline") && strcmp(status, "throttled");
        if (!br.caps_checked) br.poll_at = time_now_ms() + SPOT_PAIR_MS;
        /* download alone: Download Playlist; every other one: the Downloads hub */
        static const char *const ops[] = { "download", "collections", "collection", "playcollection", "downloadliked", "jobs", "job", "remove" };
        unsigned caps = 0, hub = (1u << sizeof ops / sizeof *ops) - 2;
        if (ok) for (int i = 0; i < n; ++i) for (unsigned j = 0; j < sizeof ops / sizeof *ops; ++j)
            if (!strcmp(f[4 + 6 * i], "op") && !strcmp(f[5 + 6 * i], ops[j])) caps |= 1u << j;
        br.can_download = caps & 1;
        br.downloads = (caps & hub) == hub;
        if (!br.edit) { /* the level shown again, a click on its old rows dropped, so rows and go() agree */
            stop_timer(&br.go);
            if (br.view) top()->sel = widget_get_prop_int(br.view, "_ringnav_index", 0);
            show();
        }
    } else if (act) { /* never sent again: play, enqueue, shuffle, repeat and signout change things */
        if (ok && act == A_SIGNOUT) br.until = time_now_ms() + SPOT_SIGNOUT_MS;
        if (login || (ok && act == A_SIGNOUT))
            base(S_PAIR);
        else if (ok && act == A_SET)
            l->state = S_LOAD, show();
        else if (ok && act == A_PLAY) {
            sp.loading = 1; /* not a timed-out play's 2, which spot_started takes as any track */
            sp.loading = !spot_started();
            sp.load_at = time_now_ms();
            sp.log_from = log_size();
            spot_np();
        }
        else if (ok && act == A_REMOVE) {
            br.selected[0] = 0;
            while (br.depth > 1 && top()->kind != L_STORAGE) drop(&br.lv[--br.depth]);
            show();
            toast(msg);
        } else if (ok && act == A_PREVIEW) { /* its own act: no other reply opens Remove */
            tk_snprintf(br.remove_note, sizeof br.remove_note, "%s", msg);
            push(L_REMOVE, "", "", "", "");
        } else if (!ok || act == A_DOWNLOAD)
            toast(msg);
    } else if (l->id != wk.id) {
    } else if (wk.fail == W_NOSOCK && (int)(br.until - time_now_ms()) > 0) {
        if (l->state != S_PAIR) l->state = S_WAIT, show(); /* librespot starting */
        br.poll_at = time_now_ms() + 500;
    } else if (wk.fail == W_NOSOCK && !access(SPOT_STATE, 0))
        sp.old = 1, base(S_OLD);
    else if (login)
        base(S_PAIR);
    else if (ok) {
        if (l->kind == L_LIST || l->kind == L_SELECT) {
            stop_timer(&br.go); /* a queued click belongs to the old rows, before this refresh */
            if (f != l->f) memcpy(l->f, f, sizeof l->f);
            free(l->buf), l->buf = reply, reply = 0, l->n = n;
        }
        l->state = S_READY;
        br.poll_at = time_now_ms() + 2000;
        if (l->kind == L_BASE && !br.caps_checked) request("caps", "", A_CAPS);
        else show();
    } else if (l->state == S_READY) /* Download Progress's refresh: the list stays */
        br.poll_at = time_now_ms() + 2000;
    else {
        l->state = S_ERROR;
        l->retry = n < 0 || !strcmp(status, "offline") || !strcmp(status, "throttled") || !strcmp(status, "error");
        tk_snprintf(l->note, sizeof l->note, "%s", msg);
        show();
    }
    free(reply);
}

static int go(const void *unused);

static void later(int next, int arg) {
    br.next = next, br.arg = arg;
    if (br.page) rearm(&br.go, go, 0); /* not once closed: the box's blur can come after */
}

static int clicked(void *ctx, void *event) {
    later(G_ROW, (int)(long)ctx);
    return 0;
}

/* The search box's OK, or its keyboard closed with text: its categories, Return going back to the
 * menu. An empty OK closes the keyboard with no other event, so it is taken here too. */
static int searched(void *ctx, void *event) {
    if (widget_get_text_utf8(br.edit, br.term, sizeof br.term)) br.term[0] = 0;
    later(br.term[0] ? G_SEARCH : G_SHOW, 0);
    return 0;
}

/* The box left without a search: the menu as it was. */
static int search_left(void *ctx, void *event) {
    if (!br.go) later(G_SHOW, 0);
    return 0;
}

/* A row's choice, on a timer: a row's click may not rebuild the list it is in. */
static int go(const void *unused) {
    static const char *const ops[] = { 0, "liked", "playlists", "albums", "artists", 0, "queue", "settings", "account", "downloaded" };
    static const char *const cats[] = { "tracks", "albums", "artists", "playlists" };
    level_t *l = top();
    int i = br.arg;
    char a[sizeof l->args] = "";
    br.go = 0;
    if (br.next == G_SHOW)
        show();
    else if (br.next == G_SEARCH)
        push(L_CATS, "", "", "", br.term);
    else if (l->state == S_ERROR) /* Retry */
        l->state = S_LOAD, show();
    else if (l->state == S_OLD || l->state == S_PAIR) {
        if (l->state == S_OLD && !i) spot_np();
    } else if (l->kind == L_BASE) {
        int r = br.rows[i];
        if (r == M_PLAYING)
            spot_np();
        else if (r == M_DOWNLOADS)
            push(L_DOWNLOADS, "", "", "", "Downloads");
        else if (r != M_SEARCH)
            push(L_LIST, ops[r], "", "", MENU[r]);
        else { /* peq_ui.c's T9 edit over the title, its keyboard open */
            l->sel = widget_get_prop_int(br.view, "_ringnav_index", 0);
            widget_destroy_children(br.page), br.view = 0; /* the box alone: the keyboard leaves no room for the rows */
            br.edit = edit_create(br.page, "text", 2, 26);
            widget_set_prop_str(br.edit, "tips", "Search Spotify");
            widget_on(br.edit, EVT_VALUE_CHANGED, searched, 0);
            widget_on(br.edit, EVT_IM_ACTION, searched, 0);
            widget_on(br.edit, EVT_BLUR, search_left, 0);
            widget_set_focused(br.edit, 1);
        }
    } else if (l->kind == L_CATS) {
        if (put(a, sizeof a, cats[i]) && put(a, sizeof a, br.term)) push(L_LIST, "search", a, "", CATS[i]);
    } else if (l->kind == L_DOWNLOADS) {
        if (!br.downloads) {
            if (!i) push(L_LIST, "downloaded", "", "", "Downloaded Tracks");
            else if (i == 1 && br.can_download) request("downloadstatus", "", A_DOWNLOAD);
        }
        else if (i == 3) push(L_STORAGE, "", "", "", "Storage Management");
        else push(L_LIST, i == 0 ? "collections" : i == 1 ? "downloaded" : "jobs", "", "", i == 0 ? "Downloaded Playlists" : i == 1 ? "Downloaded Tracks" : "Download Progress");
    } else if (l->kind == L_STORAGE) {
        br.selected[0] = 0;
        tk_snprintf(br.remove_kind, sizeof br.remove_kind, "%s", i == 0 ? "collections" : i == 1 ? "tracks" : "all");
        if (i == 2) remove_request(0);
        else push(L_SELECT, i ? "downloaded" : "collections", "", "", i ? "Select Tracks" : "Select Playlists");
    } else if (l->kind == L_REMOVE) {
        if (i == 2) remove_request(1);
        else if (i == 1) pop();
    } else if (l->kind == L_JOB) {
        static const char *const actions[] = { "pause", "resume", "cancel", "retry", "update" };
        tk_snprintf(a, sizeof a, "%s", l->args);
        if (put(a, sizeof a, actions[i])) request("job", a, A_DOWNLOAD);
    } else if (l->kind == L_SELECT && i == l->n + more(l)) {
        if (br.selected[0]) remove_request(0);
        else toast("Select at least one item");
    } else if (l->kind == L_SELECT && i < l->n) {
        l->sel = i;
        if (csv_toggle(br.selected, sizeof br.selected, cell(l, i, 1)) < 0) toast("Selection full. Remove these first");
        show();
    } else if (!strcmp(l->op, "collection") && i == l->n + more(l)) {
        push(L_JOB, "", l->args, "", "Download Controls");
    } else if (!strcmp(l->op, "liked") && br.downloads && i == l->n + more(l)) {
        request("downloadliked", "", A_DOWNLOAD);
    } else if (l->kind == L_CONFIRM) {
        if (i)
            request("signout", "", A_SIGNOUT);
        else
            pop();
    } else if (!strcmp(l->op, "playlist") && i == l->n + more(l))
        request("download", l->args, A_DOWNLOAD);
    else if (i == l->n) { /* More…, or Sign out */
        char c[sizeof l->cursor] = "";
        if (!l->f[3][0])
            push(L_CONFIRM, "", "", "", "");
        else if (put(c, sizeof c, l->f[3]))
            push(l->kind, l->op, l->args, c, l->title);
    } else {
        const char *kind = cell(l, i, 0), *uri = cell(l, i, 1), *v = cell(l, i, 2), *ctx = cell(l, i, 4);
        int queue = !strcmp(l->op, "queue"), dl = !strcmp(l->op, "downloaded"), shuffle = !strcmp(uri, "shuffle");
        if (opens(kind)) { /* a job its controls; an album, playlist, artist or collection its list */
            int job = !strcmp(kind, "job");
            if (put(a, sizeof a, uri)) push(job ? L_JOB : L_LIST, job ? "" : kind, a, "", job ? ctx : v);
        } else if (!strcmp(kind, "track") && !strcmp(l->op, "collection")) {
            if (put(a, sizeof a, ctx) && put(a, sizeof a, cell(l, i, 5))) {
                request("playcollection", a, A_PLAY);
                tk_snprintf(sp.wanted, sizeof sp.wanted, "%s", strstr(uri, "spotify:track:") == uri ? uri + 14 : "");
            }
        } else if (!strcmp(kind, "missing")) {
            toast(cell(l, i, 3));
        } else if (!strcmp(kind, "track")) { /* in its context, or alone; a queue row by its place; a download has no context */
            if (queue ? put(a, sizeof a, cell(l, i, 5)) && put(a, sizeof a, uri)
                : ctx[0] ? put(a, sizeof a, ctx) && put(a, sizeof a, cell(l, i, 5)) && put(a, sizeof a, uri)
                         : put(a, sizeof a, uri))
                request(dl ? "playdownload" : queue ? "skip" : "play", a, A_PLAY); /* Now Playing once it plays */
        } else if (!strcmp(kind, "setting"))
            request(shuffle ? "shuffle" : "repeat",
                    shuffle              ? strcmp(v, "on") ? "\ton" : "\toff"
                    : !strcmp(v, "off")     ? "\tcontext"
                    : !strcmp(v, "context") ? "\ttrack"
                                            : "\toff",
                    A_SET);
    }
    return 0;
}

/* The page's timer: a finished request taken, the pairing prompt asking again, caps asked again
 * when a request cancelled it, Download Progress refreshed while it shows, and the menu following
 * Now Playing. */
static int br_tick(const void *unused) {
    level_t *l = top();
    if (wk.done) {
        if (wk.running) pthread_join(wk.thread, 0);
        wk.running = wk.done = 0;
        got();
    } else if (!wk.running && (int)(time_now_ms() - br.poll_at) >= 0 &&
               (l->state == S_WAIT || ((l->state == S_PAIR || l->state == S_OLD) && g_backlight_status &&
                                       window_manager_get_top_window(window_manager()) == br.page)))
        fetch(l);
    else if (!wk.running && !br.go && !br.edit && br.depth == 1 && l->state == S_READY && !br.caps_checked &&
             (int)(time_now_ms() - br.poll_at) >= 0 && g_backlight_status &&
             window_manager_get_top_window(window_manager()) == br.page)
        request("caps", "", A_CAPS);
    else if (!wk.running && !br.go && l->state == S_READY && !strcmp(l->op, "jobs") &&
             (int)(time_now_ms() - br.poll_at) >= 0 && g_backlight_status &&
             window_manager_get_top_window(window_manager()) == br.page) {
        if (br.view) l->sel = widget_get_prop_int(br.view, "_ringnav_index", 0);
        fetch(l);
    } else if (br.depth == 1 && l->state == S_READY && !br.edit && has_track() != br.playing)
        l->sel = widget_get_prop_int(br.view, "_ringnav_index", 0), show();
    return 8; /* RET_REPEAT */
}

static int br_leave(const void *unused) {
    br.leave = 0;
    if (br.edit) /* the search box emptied, so no search; its blur shuts the keyboard, then the box */
        widget_set_text_utf8(br.edit, ""), widget_set_focused(br.edit, 0);
    else if (br.depth > 1)
        pop();
    else
        navigator_back();
    return 0;
}

/* Return: the search box shut, else a level back, the base back to Streaming. */
static int br_keyup(void *ctx, void *event) {
    return return_key(event, &br.leave, br_leave);
}

static int br_closed(void *ctx, void *event) {
    fetch_cancel();
    stop_timer(&br.timer);
    stop_timer(&br.go);
    stop_timer(&br.leave);
    while (br.depth) drop(&br.lv[--br.depth]);
    free(br.lv);
    memset(&br, 0, sizeof br);
    return 0;
}

/* Streaming's Spotify row (navigation.c ringnav_stream): librespot started if it is not, and the
 * browser; without librespot on the card, Now Playing says so. */
int spot_open(void *ctx, void *event) {
    if (access(SPOT_BIN, 0) && !sp.launched) return spot_np(), 0;
    spot_launch();
    if (br.page || !(br.lv = calloc(SPOT_DEPTH, sizeof *br.lv))) return 0;
    if (!(br.page = page_open("spotbrowse_page", br_closed, br_keyup))) return free(br.lv), br.lv = 0, 0;
    br.depth = 1;
    br.until = sp.launch_at + SPOT_OLD_MS;
    tk_snprintf(br.lv[0].op, sizeof br.lv[0].op, "account");
    br.timer = timer_add(br_tick, 0, 250);
    show();
    return 0;
}

/* ringnav_keylong: Play/Pause held on a track row adds it to Spotify's queue, on a playlist row
 * downloads it, the reply toasted; 1 when it was one. */
int spot_hold(void *w) {
    level_t *l = br.page && w == br.page ? top() : 0;
    int i = l && l->kind == L_LIST && l->state == S_READY && br.view
                ? widget_get_prop_int(br.view, "_ringnav_index", -1)
                : -1;
    char a[sizeof l->args] = "";
    const char *kind = i >= 0 && i < l->n ? cell(l, i, 0) : "";
    int track = !strcmp(kind, "track");
    if ((!track && strcmp(kind, "playlist")) || !put(a, sizeof a, cell(l, i, 1))) return 0;
    /* a playlist downloads, as its own Download Playlist row; its reply is toasted */
    request(track ? "enqueue" : "download", a, track ? A_ENQUEUE : A_DOWNLOAD);
    return 1;
}

/* iPod's Now Playing corners (navigation.c paint_cover) round the art too. */
void *spot_art(void) { return ui.art; }

static struct {
    unsigned until;
    int playing;
} control __attribute__((section(".scratch")));
int spot_status(void) {
    int playing =
        control.until && time_now_ms() - control.until < 1000 ? control.playing : sp.playing;
    return sp.active ? (playing ? 2 : 3) : 0;
}
unsigned spot_session(void) { return fnv(FNV_SEED, (const unsigned char *)sp.track); }
void spot_set_playing(int playing) {
    if (sp.active && playing != (spot_status() == 2)) {
        dgram_send(&sp.sock, SPOT_SOCK, playing ? "P" : "s", 1);
        control.until = time_now_ms();
        control.playing = playing;
    }
}

#endif
