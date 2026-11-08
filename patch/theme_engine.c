/* The theme engine: Light Mode's flip, the themes table, the settings it reads, accent(), theme(),
 * scheme() and the accent's tones. */
#include "navigation.h"

/* A one-digit setting under section in the stock config.ini, 0 to n - 1, else def (a missing key
 * too): toolsReadConfig(path, section, key, out, default) copies the value, or the default. */
int config_value(const char *section, const char *key, int n, int def) {
    char s[256] = "", d[2] = { (char)('0' + def), 0 };
    toolsReadConfig("/mnt/data/config.ini", section, key, s, d);
    return s[0] >= '0' && s[0] < '0' + n && !s[1] ? s[0] - '0' : def;
}

#define ROCKBOX_BIN "/mnt/mmc/.rockbox/rockbox"
/* glibc 2.28 MIPS o32 stat v3: st_mode at byte 20, as stock's fs stat reads it. */
int rockbox_available(void) {
    unsigned info[36];
    return !__xstat(3, ROCKBOX_BIN, info) && (info[5] & 0170000) == 0100000;
}

/* Light Mode (docs/ipod.md#light-mode): a neutral colour, its channels within LIGHT_SPREAD, flipped
 * per channel from black to the light half's paper and white to its ink (LIGHT_PAPER and LIGHT_INK
 * unless the scheme sets them); any other hue, and the alpha byte, as they are. Either byte order
 * (color_t or 0xRRGGBB): the paper and ink are grey. */
int neutral(unsigned c) {
    unsigned hi = 0, lo = 255;
    for (int s = 0; s < 24; s += 8) {
        unsigned v = c >> s & 255;
        hi = v > hi ? v : hi;
        lo = v < lo ? v : lo;
    }
    return hi - lo <= LIGHT_SPREAD;
}
unsigned flip_v(unsigned v, unsigned paper, unsigned ink) {
    return paper - v * (paper - ink) / 255;
}
static const scheme_t *light_row(void);
unsigned flip(unsigned c) {
    if (!neutral(c)) return c;
    /* greys, so either byte order */
    const scheme_t *S = light_row();
    unsigned paper = S->paper & 255, ink = S->ink & 255;
    unsigned out = c & 0xff000000u;
    for (int s = 0; s < 24; s += 8) out |= flip_v(c >> s & 255, paper, ink) << s;
    return out;
}
unsigned flip_if(int on, unsigned c) { return on ? flip(c) : c; }
/* The grey whose flip is v, by the half's paper and ink; v itself when dark. */
unsigned unflip(unsigned v) {
    if (!ipod_light()) return v;
    const scheme_t *S = light_row();
    unsigned paper = S->paper & 255, ink = S->ink & 255;
    return v < ink ? 255 : v > paper ? 0 : (paper - v) * 255 / (paper - ink);
}

/* Every colour the payload draws passes here (peq.h wraps the canvas setters), as stock's pass
 * the style and image hooks: flipped in Light Mode. A clear colour stays 0: stock tests for it
 * (Now Playing's slider draws no dragger under a #00000000 border). */
unsigned ui_ink(unsigned c) { return !st.raw_ink && ipod_light() && c >> 24 ? flip(c) : c; }
/* Sets whether ui_ink leaves colours alone (final colours follow); the last setting, to restore. */
unsigned ui_ink_raw(unsigned raw) {
    unsigned old = st.raw_ink;
    st.raw_ink = raw;
    return old;
}

/* The 0xRRGGBB j/n of the way from one color to another, per channel. Shared with visualizer.c. */
unsigned mix(unsigned from, unsigned to, int j, int n) {
    unsigned c = 0;
    for (int s = 0; s < 24; s += 8)
        c |= (unsigned)(((int)(from >> s & 255) * (n - j) + (int)(to >> s & 255) * j) / n) << s;
    return c;
}

/* The Accent, Home, Battery and Theme settings; each theme keeps its own accent
 * (docs/ipod.md#display-settings). */
static const unsigned accents[][5] = { ACCENTS };
_Static_assert(sizeof accents / sizeof *accents == ACCENT_N, "one accent per ACCENTS row");
const char *const accent_names[] = { "Accent: Graphite",  "Accent: Crimson", "Accent: Tidal",
                                     "Accent: Champagne", "Accent: Cobalt",  "Accent: Tangerine",
                                     "Accent: Plum",      "Accent: Ivory",   "Accent: Green" };
_Static_assert(sizeof accent_names / sizeof *accent_names == ACCENT_N, "one name per ACCENTS row");
_Static_assert(sizeof st.setting_label / sizeof *st.setting_label == SETTING_OWN + THEME_KEYS,
               "one label per Display row");
#include "themes.inc" /* tools/theme.py, from assets/themes/<id>/theme.xml */
_Static_assert(DRIFT_TEXELS >= 4 * (375 - HOME_SPLIT_W) * CLIENT_H_MAX,
               "Home drift frame fits every set");
#if __SIZEOF_POINTER__ == 4 /* the Q2's layout; test/fw's 64-bit build has its own */
_Static_assert(__builtin_offsetof(theme_t, geom) == THEME_GEOM_OFF, "test/patch.py's set_geom");
_Static_assert(__builtin_offsetof(theme_t, motion) == THEME_MOTION_OFF && sizeof(motion_t) == 8 &&
                   __builtin_offsetof(theme_t, recall_ms) == THEME_MOTION_OFF + MOTION_N * 8 &&
                   __builtin_offsetof(theme_t, bump_px) == THEME_MOTION_OFF + MOTION_N * 8 + 2,
               "test/patch.py's set_motion, set_recall and set_bump");
_Static_assert(__builtin_offsetof(scheme_t, mode) == SCHEME_MODE_OFF, "test/patch.py patches it");
#endif
/* A value of the set theme_boot wrote (st.geom), never the live theme's: V_BAR, ... */
int geom_val(int v) { return geom_vals[st.geom][v]; }
int client_h(void) { return 320 - geom_val(V_BAR); }
/* The Display rows' settings, by ringnav_display's index: 0 Accent, 1 Home (0 Split, 1 Full,
 * 2 Carousel), 2 Battery, 3 Theme, 4 Shortcut, 5 Corners, 6 Light Mode, 7 Smart Awake, 8 Centred
 * Now Playing, 9 Click With Headphones (Controls, under Key Tone), 10 Single Press Scrub, 11 Time
 * in Title; 12 on: a theme's own options, SETTING_OWN + k for theme_keys[k]. */
static const char *const setting_keys[] = { "ACCENT",    "HOME",      "BATTERY",     "THEME",
                                            "SHORTCUT",  "ROUNDED",   "LIGHT",       "KEEPLIT",
                                            "NPCENTRED", "KEYTONEHP", "SCRUBSINGLE", "TIMETITLE" };
const int setting_counts[] = { SETTING_N_ACCENT,
                               SETTING_N_HOME,
                               SETTING_N_BATTERY,
                               THEME_N,
                               SETTING_N_SHORTCUT,
                               SETTING_N_CORNERS,
                               SETTING_N_LIGHT,
                               2,
                               SETTING_N_NP_LAYOUT,
                               2,
                               2,
                               2 };
int *const setting_values[] = { &st.accent,     &st.home,       &st.battery,      &st.theme,
                                &st.shortcut,   &st.rounded,    &st.light,        &st.keep_lit,
                                &st.np_centred, &st.keytone_hp, &st.scrub_single, &st.time_title };
/* An option's value and config key: the accent's are the theme's own, an own option's st.own[] and
 * theme_keys[], the rest setting_values'. */
int *option_slot(const theme_t *T, int setting) {
    return setting == SETTING_ACCENT ? T->accent_slot
           : setting >= SETTING_OWN  ? &st.own[setting - SETTING_OWN]
                                     : setting_values[setting];
}
const char *option_key(const theme_t *T, int setting) {
    return setting == SETTING_ACCENT ? T->accent_key
           : setting >= SETTING_OWN  ? theme_keys[setting - SETTING_OWN].key
                                     : setting_keys[setting];
}
/* The option bound to setting, or NULL. */
const option_t *option_of(const theme_t *T, int setting) {
    for (int k = 0; k < T->n_opts; ++k)
        if (T->opts[k].setting == setting) return &T->opts[k];
    return (void *)0;
}
/* The local time now into buf (struct tm: tm_sec @0, tm_min @4, tm_hour @8), or 0 when the clock
 * cannot be read. */
const int *clock_now(int *buf) {
    long long wall = wall_time();
    long now = clock_back(wall);
    return wall == -1 ? (void *)0 : localtime_r(&now, buf);
}
/* Light Mode's Auto (LIGHT=2) is light from 7:00 to 19:00 by the clock. clock_show applies it each
 * minute: when Auto crosses either time it drops the cached images (ringnav_image_add bakes the
 * mode into them) and repaints, as setting_click does. */
#define LIGHT_FROM 7
#define LIGHT_UNTIL 19
int light_day(const int *tm) { return tm && tm[2] >= LIGHT_FROM && tm[2] < LIGHT_UNTIL; }
/* Night Shift's Auto (nightshift.c) is on while Light Mode's would be dark, 19:00 to 7:00, and
 * off while the clock cannot be read. */
int clock_night(void) {
    int buf[11];
    const int *tm = clock_now(buf);
    return tm && !light_day(tm);
}
void light_apply(const int *tm) {
    if (st.light != 2 || light_day(tm) == st.light_day) return;
    st.light_day = !st.light_day;
    np_fill(0);
    text_inks(window_manager());
    image_manager_unload_all(image_manager());
    widget_invalidate_force(window_manager(), (void *)0);
}
/* accent()'s first call: the [IPOD] keys. Its own function, so accent()'s every later call (each
 * colour Light Mode flips asks twice: ipod_light(), scheme()) skips this frame. */
__attribute__((noinline)) static void settings_read(void) {
    for (int i = 0; i < 4; ++i)
        *setting_values[i] = config_value("IPOD", setting_keys[i], setting_counts[i], 0);
    _Static_assert(SETTING_N_SHORTCUT <= 10, "config_value reads one digit");
    st.shortcut = config_value("IPOD", "SHORTCUT", SETTING_N_SHORTCUT,
                               SETTING_N_SHORTCUT); /* no key, see shortcut() */
    st.minimal_accent = config_value("IPOD", "MINIMAL_ACCENT", ACCENT_N, MINIMAL_ACCENT);
    st.rounded = config_value("IPOD", "ROUNDED", 2, 0); /* Square unless chosen */
    st.light = config_value("IPOD", "LIGHT", 3, 0);     /* dark unless chosen; 2 Auto */
    st.keep_lit = config_value("IPOD", "KEEPLIT", 2, 0);
    st.np_centred = config_value("IPOD", "NPCENTRED", 2, 0);
    st.keytone_hp = config_value("IPOD", "KEYTONEHP", 2, 0);
    st.scrub_single = config_value("IPOD", "SCRUBSINGLE", 2, 0);
    st.time_title = config_value("IPOD", "TIMETITLE", 2, 1); /* the clock unless chosen */
    /* the themes' own keys, after the thirteen, in table order */
    for (int k = 0; k < THEME_KEYS; ++k)
        st.own[k] = config_value("IPOD", theme_keys[k].key, theme_keys[k].n, theme_keys[k].def);
    /* A stored value outside an option's values reads as its default (Classic's Ivory: Classic
     * Blue), the key untouched. A payload key is every theme's: only the live theme's reading
     * counts. */
    for (int t = 0; t < THEME_N; ++t)
        for (int k = 0; k < themes[t].n_opts; ++k) {
            const option_t *o = &themes[t].opts[k];
            int *v = option_slot(&themes[t], o->setting);
            if (o->values && o->def >= 0 && !(o->values >> *v & 1) &&
                (t == st.theme || o->setting == SETTING_ACCENT))
                *v = o->def;
        }
    int buf[11];
    st.light_day = light_day(clock_now(buf));
    st.settings_read = 1;
}
int accent(void) {
    if (!st.settings_read) settings_read();
    return *themes[st.theme].accent_slot;
}
const theme_t *theme(void) {
    accent();
    return &themes[st.theme];
}
/* 1 for scheme v's light half: its mode, else Light Mode (Auto: light_day), which reads 0 where the
 * theme does not declare it (setting()'s rule). The dark row's mode: both carry it. */
static int light_on(const theme_t *T, int v) {
    int m = T->schemes[v].mode;
    return m == SCHEME_LIGHT || (m == SCHEME_DERIVE && !(T->undeclared >> SETTING_LIGHT & 1) &&
                                 (st.light == 2 ? st.light_day : st.light));
}
/* The half Light Mode picks; Minimal's and Modern's two halves are one row. */
const scheme_t *scheme(void) {
    int v = accent();
    const theme_t *T = &themes[st.theme];
    return &(light_on(T, v) ? T->light : T->schemes)[v];
}
/* scheme() for code that runs only in the light half (flip, red_map's light term), so after
 * ipod_light()'s accent(): the light row without a second accent() and light_on() on these
 * per-colour paths (a Classic folder paint sets ~470 colours). */
static const scheme_t *light_row(void) {
    const theme_t *T = &themes[st.theme];
    return &T->light[*T->accent_slot];
}
/* An option the theme does not declare reads 0, its stored key untouched (ponytail: 0 is every
 * hidden option's value today; a theme hiding Home or Battery needs a value per bit). */
int setting(int i) {
    accent();
    return themes[st.theme].undeclared >> i & 1 ? 0 : *setting_values[i];
}
/* A theme's own choice (OWN_*): its stored value while the theme offers it, else 0. */
static int theme_own(int k) { return option_of(theme(), SETTING_OWN + k) ? st.own[k] : 0; }
/* Classic's Home in Split (coverflow.c): Library on Home and Row Preview */
int home_lib_on(void) { return theme_own(OWN_HOMELIB); }
int row_preview_on(void) { return theme_own(OWN_ROWPREVIEW); }
const char *const np_pages[] = { "playing_page", "playing_page_stock" }; /* [NP_*] */
_Static_assert(sizeof np_pages / sizeof *np_pages == NP_N, "one page per NP_* layout");
/* The Now Playing page: the theme's layout, which Centred Now Playing forces to NP_CENTRED. */
int np_layout(void) { return setting(SETTING_NPCENTRED) ? NP_CENTRED : theme()->np_layout; }
/* Shortcut with no key is Rockbox while the card has it, else Favorites: asked each time, as the
 * card may mount after the first read. */
int shortcut(void) {
    accent();
    return st.shortcut < SETTING_N_SHORTCUT ? st.shortcut : rockbox_available() ? 0 : 3;
}
int ipod_home(void) {
    accent();
    return st.home;
}
int ipod_light(void) {
    int v = accent();
    return light_on(&themes[st.theme], v);
}
int ipod_battery(void) {
    accent();
    return st.battery;
} /* lockscreen.c's Large clock */
/* 1 when Home shows its Rockbox row (coverflow_home_layout): the card has Rockbox. */
int ipod_home_rockbox(void) {
    st.home_rockbox = rockbox_available();
    return shortcut() || st.home_rockbox;
}
const char *ipod_home_shortcut(void) {
    static const char *const names[] = { "Rockbox",      "Artists",   "Albums", "Favorites",
                                         "Rated Albums", "Shuffle",   "Games",  "Playlists",
                                         "Podcasts",     "Audiobooks" };
    _Static_assert(sizeof names / sizeof *names == SETTING_N_SHORTCUT, "one per shortcut");
    return names[shortcut()];
}
/* Podcasts, Audiobooks and Videos (docs/internals.md#podcasts-and-audiobooks): the card's top-level
 * folders of these names, case aside, browsed in folder_page from there; Photos opens photos.c,
 * Books books.c. */
const char *const MEDIA[] = { "Podcasts", "Audiobooks", "Photos", "Books", "Videos" };
extern int rockbox_open(void *, void *);
int rated_albums(void *, void *);
int home_shortcut_open(void *ctx, void *event) {
    int s = shortcut();
    if (!s) return rockbox_open(ctx, event);
    if (s == 4) return rated_albums(ctx, event);
    if (s == 5) return shuffle_menu(ctx, event);
    if (s == 6) return games_open(ctx, event);
    if (s == 8 || s == 9) return media_click((void *)(long)(s == 8 ? PODCASTS : AUDIOBOOKS), event);
    if (s == 7)
        return navigator_to_with_context("localmusic/playlist_page",
                                         (void *)0); /* as stock's Home */
    if (s == 2) {
        struct {
            int cls;
            void *record;
        } album = { 0xf003, 0 };
        return navigator_to_with_context("localmusic/album_page", &album);
    }
    return navigator_to_with_context(s == 1 ? "localmusic/localclass_page"
                                            : "localmusic/allmusic_page",
                                     (void *)(long)(s == 1 ? 0xf004 : 0xf00a));
}
/* Light Mode keeps a coloured accent's tones; a neutral one (Graphite, Ivory) takes its silver red
 * tone for both, which flips dark. Before flip. */
unsigned tone_of(const unsigned *a, int tone) {
    return ipod_light() && neutral(a[0]) ? a[TONE_RED] : a[tone];
}
unsigned accent_tone(int tone) {
    const scheme_t *S = scheme();
    return tone == TONE_LIGHT && S->flags & SCHEME_CARD ? S->fill : tone_of(S->tones, tone);
}

/* A color_t (bytes r, g, b, a) that is stock red blended with a neutral, t * red + k * white per
 * channel within RED_TOLERANCE, becomes the same blend of one of the preset's tones (TONE_RED for
 * text and images, TONE_LIGHT for every other color): flat reds,
 * pressed tints and anti-aliased edges follow the accent, alpha is kept, and greys and other hues
 * are unchanged. t comes from least squares against red with the mean removed, in 1/4096. The white
 * part (k) becomes that share of glyph: white keeps a blend's light part as it is. With another
 * glyph, neutral pixels (the glyph itself) map too, so pass it only for an image that holds red.
 * Crimson is the identity. Light Mode (light) puts the red share over paper instead: a tint over
 * black becomes the same tint of tone over the light half's paper, its grey part flipped. */
unsigned red_map(unsigned c, unsigned tone, unsigned glyph, int light) {
    int paper = light ? (int)(light_row()->paper & 255) : 0,
        ink = light ? (int)(light_row()->ink & 255) : 0;
    static const int red[3] = { STOCK_RED >> 16, STOCK_RED >> 8 & 255, STOCK_RED & 255 };
    const int sum = red[0] + red[1] + red[2];
    int ch[3] = { c & 255, c >> 8 & 255, c >> 16 & 255 }, s = ch[0] + ch[1] + ch[2], d = 0, dd = 0;
    for (int i = 0; i < 3; ++i) {
        d += (3 * ch[i] - s) * (3 * red[i] - sum);
        dd += (3 * red[i] - sum) * (3 * red[i] - sum);
    }
    int t = d * 4096 / dd;
    if (t < 256 && glyph != 0xffffff) t = 0; /* a neutral glyph pixel takes the glyph colour too */
    int k = (s * 4096 - t * sum) / (3 * 4096);
    if ((t && t < 256) || t > 4096 + 256 || k < -RED_TOLERANCE) return c;
    unsigned out = c & 0xff000000u;
    for (int i = 0; i < 3; ++i) {
        int miss = ch[i] - (t * red[i] / 4096 + k);
        if (miss > RED_TOLERANCE || miss < -RED_TOLERANCE) return c;
        int v = t * (int)(tone >> (16 - 8 * i) & 255) / 4096 +
                (light ? (t < 4096 ? 4096 - t : 0) * paper / 4096 - k * (paper - ink) / 255
                       : k * (int)(glyph >> (16 - 8 * i) & 255) / 255);
        out |= (unsigned)(v < 0 ? 0 : v > 255 ? 255 : v) << 8 * i;
    }
    return out;
}
unsigned accent_map(unsigned c, int preset, int tone) {
    const theme_t *T = theme(); /* once: this runs per colour */
    const unsigned *a = T->schemes[preset].tones;
    if (ipod_light())
        return !(c >> 24) || neutral(c) ? ui_ink(c)
                                        : red_map(c, flip(tone_of(a, tone)), 0xffffff, 1);
    return T->schemes[preset].flags & SCHEME_RAW_RED ? c : red_map(c, a[tone], 0xffffff, 0);
}
