#ifndef Q2_THEME_H
#define Q2_THEME_H
/* The built-in themes' shape (docs/internals.md#themes); the table is navigation.c's themes[]. */

enum { PAINT_SELECTION, PAINT_BAR, PAINT_BATTERY, PAINTER_N }; /* theme_t.painter[] index */
/* painter[PAINT_SELECTION]: Minimal's card, Classic's stops, Modern's bar */
enum { SEL_CARD, SEL_STOPS, SEL_BAR };
enum { BAR_FLAT, BAR_GLOSS }; /* painter[PAINT_BAR]: the status bar's fill (Classic gloss) */
enum {
    BATT_STOCK,
    BATT_CLASSIC_LIGHT
}; /* painter[PAINT_BATTERY]: the bar's battery and status marks */
enum { SCHEME_CARD = 1, SCHEME_RAW_RED = 2 };      /* scheme_t.flags (Ivory in Minimal; Crimson) */
enum { SCHEME_DERIVE, SCHEME_LIGHT, SCHEME_DARK }; /* scheme_t.mode */
enum { NP_IPOD, NP_CENTRED, NP_N };                /* theme_t.np_layout: np_pages[] index */
enum {
    FONT_DEFAULT,
    FONT_BOLD,
    FONT_CLASSIC_LIGHT
}; /* theme_t.font[]: stock's, bold.ttf, classic-light.ttf */
/* theme_t.font[] index: list title, list second line, bar title, Now Playing title and Home rows, a
 * two-line row's title */
enum { ROLE_TITLE, ROLE_META, ROLE_BAR, ROLE_NP, ROLE_TALL, ROLE_N };
/* an N-stop gradient, n stops (0: none), pos rising from 0 to 100 (% of the band), rgb 0xRRGGBB;
 * as many stops as the band has rows: each row its own, exactly (paint_stops) */
typedef struct {
    unsigned char n, pos[13];
    unsigned rgb[13];
} stops_t;
typedef struct {
    const unsigned *tones; /* its ACCENTS row: accents[v] (top, bottom, light, red, highlight) */
    unsigned char flags;   /* SCHEME_* */
    unsigned char mode; /* SCHEME_DERIVE: Light Mode picks the half; LIGHT/DARK: that half always.
                           Both halves carry it; light_on() reads the dark row's */
    /* 0xRRGGBB; 0: role unused (ponytail: no black role) */
    unsigned fill, chevron, muted, bar_light;
    stops_t sel;       /* the stops painter's band */
    unsigned sel_top;  /* its first pixel row, a crisp line above the stops, 0 none */
    unsigned sel_edge; /* its last pixel row, 0 none */
    unsigned sel_tall_edge, sel_tall_line; /* a row taller than the set's pitch: its last two rows,
                                              0 sel_edge alone */
    unsigned sel_tall_top;                 /* ... and its second, 0 in both halves the stops */
    unsigned sel_text;                     /* text on it, 0: the row's own */
    stops_t bar;                           /* BAR_GLOSS: the status bar's rows above its line */
    unsigned bar_line;                     /* BAR_GLOSS: the bar's last pixel row */
    unsigned bar_line2;  /* BAR_GLOSS: the light half's row above it, final, 0 none */
    unsigned paper, ink; /* this half's, greys 0xRRGGBB: the light half's are the flip's ends
                            (LIGHT_PAPER and LIGHT_INK unless set), the dark half's 0x000000 and
                            0xffffff. A one-array theme's row holds the light half's: only
                            ipod_light()-gated code reads them there */
    const char *label;   /* a theme's own scheme's name ("Classic Blue"); NULL: accent_names[v] */
} scheme_t;
typedef struct {
    unsigned char setting; /* SETTING_* (navigation.c): the bound row's click/text code, or
                              SETTING_OWN + k: the theme's own option theme_keys[k] */
    signed char def;       /* stored value not in values -> def; -1 none */
    signed char preset;    /* theme_preset writes it; -1 none */
    signed char keep;      /* a stored value theme_preset leaves (Light's Auto 2); -1 none */
    unsigned values;       /* bitmask of values the row takes; 0: all of setting_counts[setting] */
} option_t;
/* a theme's own IPOD key (tools/theme.py theme_keys), read after the payload's thirteen in table
 * order into st.own[] */
typedef struct {
    const char *key;           /* the IPOD key; a theme-owned accent key's too */
    const char *label;         /* the row's title; 0: an accent key (its row is Accent's) */
    const char *const *names;  /* [n] the values' labels */
    const unsigned char *sets; /* [n] the GEOM_* set each value boots; null for a plain choice */
    unsigned char n, def;      /* value count; the value a missing or unreadable key reads */
} theme_key_t;
typedef struct {
    /* coverflow.shelf (px, brightness of 256), then coverflow.caption: offsets.inc's CF_* */
    short top, x1, xs, z1, zs, eye, bright1, bright2, reflect, reflect_top, stride, radius;
    short name_px, artist_px, text_y;
} cf_style_t;
typedef struct {
    unsigned addr;
    unsigned char group;
} boot_site_t; /* one ipod.json immediate */
typedef struct {
    const char *name;
    unsigned char sets;
} ui_redirect_t; /* sets with <set>_<name>.bin */
typedef struct {
    const char *name;   /* "Minimal": the Theme row's "Theme: %s" */
    unsigned char geom; /* second: THEME_GEOM_OFF (4) never moves */
    unsigned char painter[PAINTER_N];
    unsigned char chevron_home, chevron_sel, set_text, caps_mark, caps_home, caps_np, icons_mono,
        icons_badge_muted, icons_addon_mono, np_album_op, np_state_op; /* flags 0/1; *_op 0/255 */
    unsigned char font[ROLE_N], font_px[ROLE_N]; /* FONT_*; px 0: the style's size */
    stops_t batt;       /* BATT_CLASSIC_LIGHT: the level's stops, a hue in both halves */
    stops_t batt_empty; /* BATT_CLASSIC_LIGHT: the rows past the level */
    unsigned batt_low;  /* BATT_CLASSIC_LIGHT: the low level, batt_low_w columns */
    short batt_low_w;
    unsigned batt_line[2]; /* BATT_CLASSIC_LIGHT: the outline and nub, final: [0] dark, [1] light */
    unsigned batt_edge;    /* BATT_CLASSIC_LIGHT: its bottom row inside the outline */
    short batt_corner_a, batt_foot_a, batt_shade_a, batt_soft_a, batt_soft_top_a, batt_emboss_a,
        batt_emboss_nub_a;        /* of 255 */
    short bar_bt_dy, bar_wifi_dy; /* rows off the bar's row layout: Bluetooth, Wi-Fi */
    /* a row with art, on screen: the cover's x and its text's first and last columns; 0: stock's */
    short art_x, art_text_x, art_text_end;
    short row_text_end; /* a two-line row without art: its text's last column on screen */
    stops_t sep[2];     /* its last rows, one stop a row, final: [0] dark, [1] light; 0: none */
    /* the theme's classic_light_chevron's box on screen: a full-width list's, Split's; 0: stock's
     * list_into
     */
    short chevron_x, home_chevron_x, art_placeholder; /* stock's grey tiles (list_folder, list_song)
                                                         this size, centred, 0: as stock */
    short home_top, home_full_row, home_text_x, home_label_end[2] /* [0] split, [1] full */,
        set_top, np_bar, np_scrub;
    /* Split's panel where no cover shows (home_pane_paint), final: [0] dark, [1] light; n 0:
     * HOME_PLACEHOLDER. pane_shadow is
     * 0xRRGGBB, the shadow down its left edge; _w in columns, _a each column's alpha. pane_light_a
     * the white over the stops at the left edge, falling to 0 at the right. A row's icon's top-left
     * (0: the pane stays the current cover or the note whatever the row), the note's and the
     * count's line box in the panel, the count's px. */
    stops_t home_pane[2];
    unsigned pane_shadow;
    unsigned char pane_shadow_a[32];
    short pane_shadow_w, pane_light_a, pane_icon_x, pane_icon_y, pane_note_x, pane_note_y,
        pane_count_y, pane_count_w, pane_count_px;
    int *accent_slot;
    const char *accent_key;  /* &st.accent,"ACCENT" | &st.minimal_accent,"MINIMAL_ACCENT" */
    const scheme_t *schemes; /* [n_schemes], indexed by accent value: each scheme's dark half */
    const scheme_t *light; /* [n_schemes] each scheme's light half; == schemes when the theme states
                              no final value (Minimal, Classic) */
    unsigned char n_schemes; /* ACCENT_N, then the theme's own (Classic: 10) */
    const option_t *opts;    /* Appearance rows after Theme, in order */
    unsigned char n_opts;
    unsigned undeclared;     /* SETTING_* bits of the theme options it does not declare */
    unsigned char np_layout; /* NP_* */
    cf_style_t cf;
    unsigned char art_square; /* art takes Corners: Square */
    unsigned batt_fill;       /* 0xRRGGBB under the battery's outline; 0: the outline's own */
} theme_t;
const theme_t *theme(void);       /* accent(); return &themes[st.theme]; */
int theme_geom(const theme_t *T); /* navigation.c: the set T boots with its stored values */
const scheme_t *scheme(void);     /* theme()'s scheme accent(), in the half Light Mode picks */
int client_h(void); /* the window under the bar of the set theme_boot wrote (navigation.c) */
void paint_stops(void *canvas, int x, int y, int w, int h, const stops_t *s); /* navigation.c */
/* 0xRRGGBB to an opaque color_t, whose bytes are r, g, b, a. */
#define RGBA(c) (0xff000000u | ((c) & 255) << 16 | ((c) & 0xff00) | (c) >> 16)
#endif
