/* AirPlay (docs/internals.md#airplay-sender): what the Q2 plays, sent to AirPlay speakers (Sonos, HomePod,
 * Apple TV…) by the q2-airplay card app, a hidden one (addon.c), whose page lists the speakers. Now
 * Playing's ⋯ page's AirPlay row opens that page. The app makes CAST_FILE (peq.h);
 * demo maps it to tell the app what plays (the player's state, the volume and, in CAST_TRACK, the song's
 * tags) and keeps the PEQ filter, which feeds the app from hciplayer, in the chain. While a speaker plays
 * the volume is the speaker's, saved apart as Q2POD AIRVOL, so the Q2's own comes back when it stops;
 * the lyrics follow the speaker, which plays the song the app's delay behind the Q2. */
#include "offsets.inc"
#include "peq.h"
#include "playback.h"
#include "stock.h"

#define AIR_ID "airplay"
#define AIR_LOOK_MS 1000 /* the file looked for this often until the app makes it */
#define AIR_VOLUME 35    /* a speaker's volume before one was set */
#define AIR_TRACK_MAX 1024

extern void *list_row(void *view, const char *icon, int (*click)(void *, void *), void *ctx);

static struct {
    cast_tap *t;
    unsigned look;  /* time_now_ms of the last look for the file */
    int on;         /* a speaker playing, as the volume was last swapped */
    unsigned song;  /* the hash of the song CAST_TRACK names */
} air __attribute__((section(".scratch")));

static cast_tap *air_map(void) {
    if (air.t) return air.t;
    unsigned now = time_now_ms();
    if (now - air.look < AIR_LOOK_MS) return 0;
    air.look = now;
    if (access(CAST_FILE, 0)) return 0;
    int fd = open(CAST_FILE, 2); /* O_RDWR */
    if (fd < 0) return 0;
    unsigned head[2] = { 0, 0 }; /* the app's, whole before it renames it in (peq.h) */
    void *p = (void *)-1;
    if (read(fd, head, sizeof head) == sizeof head && head[0] == CAST_MAGIC && head[1] == sizeof(cast_tap))
        p = mmap(0, sizeof(cast_tap), 3, 1, fd, 0); /* PROT_READ | PROT_WRITE, MAP_SHARED */
    close(fd);
    return air.t = p == (void *)-1 ? 0 : p;
}

/* The app's state while it runs; CAST_OFF once it stopped beating. */
static int air_state(void) {
    cast_tap *t = air.t;
    return t && CAST_CLOCK(now_ns()) - t->want < CAST_STALE ? t->state : CAST_OFF;
}

int airplay_on(void) { return air.on; }

/* A volume (0-100) in config.ini; def without one. */
static int level(const char *section, const char *key, int def) {
    char s[256] = "";
    int v = 0;
    unsigned i;
    toolsReadConfig("/mnt/data/config.ini", section, key, s, "");
    for (i = 0; i < 3 && s[i] >= '0' && s[i] <= '9'; ++i) v = v * 10 + s[i] - '0';
    return !i || s[i] || v > 100 ? def : v;
}

/* A speaker starting: the Q2's own volume saved under its output's key (ringnav_write_config) and the
 * speaker's taken up, the DAC left as it is (ALSA's last half second before the mute plays through it;
 * the wheel sets it later, silent). Stopping: the speaker's saved and the Q2's own back, before ALSA's
 * buffer of silence runs out. */
static void air_volume(int on) {
    int v;
    if (on) {
        write_int_config(g_volume, "PLAYSET", "VOLUME");
        air.on = 1;
        v = level("Q2POD", "AIRVOL", AIR_VOLUME);
    } else {
        write_int_config(g_volume, "Q2POD", "AIRVOL");
        air.on = 0;
        v = level("PLAYSET", mclGetOutputWay() == 1 ? "BT_VOLUME" : "VOLUME", g_volume);
    }
    g_volume = (unsigned char)(v > g_maxvolume ? g_maxvolume : v);
    if (!on) device_set_volume(g_volume, 1);
}

/* "key=value\n" appended to buf at *n (cap bytes), the value's own line breaks made spaces. */
static void put(char *buf, unsigned cap, unsigned *n, const char *key, const char *value) {
    for (const char *s = key; *s && *n + 1 < cap; ++s) buf[(*n)++] = *s;
    if (*n + 1 < cap) buf[(*n)++] = '=';
    for (const char *s = value ? value : ""; *s && *n + 1 < cap; ++s)
        buf[(*n)++] = *s == '\n' || *s == '\r' ? ' ' : *s;
    if (*n < cap) buf[(*n)++] = '\n';
}

/* The playing song's tags to CAST_TRACK for the app (the speaker's now-playing and art), once a song. */
static void air_track(cast_tap *t) {
    void *q = P(mcl_pdeqplaylist, 0), *r;
    unsigned pos = (unsigned)I(MCL_POS, 0);
    if (!q || pos >= deque_size(q) || !(r = deque_at(q, pos))) return;
    const char *path = P(r, REC_PATH), *title = P(r, REC_TITLE), *artist = P(r, REC_ARTIST),
               *album = P(r, REC_ALBUM);
    if (!title || !*title) title = P(r, REC_NAME);
    unsigned h = FNV_SEED;
    h = fnv(h, (const unsigned char *)(path ? path : "")) ^ 0x2f;
    h = fnv(h * 16777619u, (const unsigned char *)(title ? title : ""));
    if (h == air.song) return;
    char buf[AIR_TRACK_MAX], len[12];
    unsigned n = 0;
    int sec = -1, total = 0;
    player_playtime_and_length(&sec, &total);
    snprintf(len, sizeof len, "%d", total > 0 ? total : 0);
    put(buf, sizeof buf, &n, "path", path);
    put(buf, sizeof buf, &n, "title", title);
    put(buf, sizeof buf, &n, "artist", artist);
    put(buf, sizeof buf, &n, "album", album);
    put(buf, sizeof buf, &n, "length", len);
    if (blob_io(CAST_TRACK, CAST_TRACK ".tmp", buf, n, 1)) air.song = h, ++t->track;
}

/* Every UI pass (ringnav_sleep). */
void airplay_poll(void) {
    cast_tap *t = air_map();
    int s = air_state();
    if (t) {
        t->status = mclGetPlayStatus();
        t->volume = air.on ? g_volume : -1; /* the speaker's once it plays, never the earphones' */
        t->maxvol = g_maxvolume;
        ++t->beat;
        if (s != CAST_OFF) {
            peq_chain(); /* the tap is in the PEQ filter, out of the chain while the EQ is off */
            air_track(t);
        }
    }
    if ((s == CAST_ON) != air.on) air_volume(s == CAST_ON);
}

/* The lyrics' place (lyrics.c lyrics_now) while a speaker plays: the speaker's, from the app; the song
 * just begun while the speaker still finishes the last. 0 when the Q2's own place stands. */
int airplay_lyrics(int *ms) {
    cast_tap *t = air.on ? air.t : 0;
    if (!t) return 0;
    unsigned at, id;
    int pos, run, tries = 0;
    do { /* the app writes pos_at last */
        at = t->pos_at, pos = t->pos_ms, id = t->pos_id, run = t->pos_run;
    } while (at != t->pos_at && ++tries < 3);
    if (id != t->id) return *ms = 0, 1;
    unsigned d = run ? CAST_CLOCK(now_ns()) - at : 0;
    pos += (int)(d < 600000 ? d * 1074u >> 10 : 0); /* ticks to ms */
    return *ms = pos > 0 ? pos : 0, 1;
}

/* The app's page, from its hidden slot; told to install it when it isn't on the card. */
static int air_open(void *ctx, void *event) {
    (void)event;
    if (access("/mnt/mmc/." AIR_ID "/q2-" AIR_ID, 0))
        toast("Install AirPlay in System settings → App Store");
    else if (!smart_hold(air_open, ctx, 0)) /* the app starts once Smart Wi-Fi's link is up */
        addon_open_id(AIR_ID);
    return 0;
}

/* Now Playing's ⋯ page (onlinematch.c ringnav_playermore): an AirPlay row, first. */
void airplay_more(void *view) {
    char text[80] = "AirPlay";
    void *label = list_row(view, "airplay", air_open, 0);
    widget_use_style(label, "s_scrlabel_white24l");
    widget_resize(label, 253, I(label, W_H));
    if (air.on && air.t->speaker[0]) snprintf(text, sizeof text, "AirPlay: %.60s", air.t->speaker);
    widget_set_text_utf8(label, text);
    widget_restack(P(P(label, W_PARENT), W_PARENT), 0);
}
