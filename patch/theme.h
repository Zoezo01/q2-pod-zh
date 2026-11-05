#ifndef Q2_THEME_H
#define Q2_THEME_H
/* The built-in themes' shape (docs/internals.md#themes); the table is navigation.c's themes[]. */

enum { PAINT_SELECTION, PAINTER_N };   /* theme_t.painter[] index */
enum { SEL_CARD, SEL_BAR, SEL_STOPS }; /* painter[PAINT_SELECTION] (Minimal card, Classic bar) */
enum { SCHEME_CARD = 1, SCHEME_RAW_RED = 2 };      /* scheme_t.flags (Ivory in Minimal; Crimson) */
enum { SCHEME_DERIVE, SCHEME_LIGHT, SCHEME_DARK }; /* scheme_t.mode */
enum { NP_IPOD, NP_CENTRED, NP_N };                /* theme_t.np_layout: np_pages[] index */
/* an N-stop gradient, n stops (0: none), pos rising from 0 to 100 (% of the band), rgb 0xRRGGBB */
typedef struct {
    unsigned char n, pos[8];
    unsigned rgb[8];
} stops_t;
typedef struct {
    const unsigned *tones; /* its ACCENTS row: accents[v] (top, bottom, light, red, highlight) */
    unsigned char flags;   /* SCHEME_* */
    unsigned char mode; /* SCHEME_DERIVE: Light Mode picks the half; LIGHT/DARK: that half always.
                           Both halves carry it; light_on() reads the dark row's */
    /* 0xRRGGBB; 0: role unused (ponytail: no black role) */
    unsigned fill, chevron, muted, bar_light;
    stops_t sel;         /* the stops painter's band */
    unsigned sel_top;    /* its first pixel row, a crisp line above the stops, 0 none */
    unsigned sel_edge;   /* its last pixel row, 0 none */
    unsigned sel_text;   /* text on it, 0: the row's own */
    unsigned paper, ink; /* this half's, greys 0xRRGGBB: the light half's are the flip's ends
                            (LIGHT_PAPER and LIGHT_INK unless set), the dark half's 0x000000 and
                            0xffffff. A one-array theme's row holds the light half's: only
                            ipod_light()-gated code reads them there */
    const char *label;   /* a theme's own scheme's name ("Nano Blue"); NULL: accent_names[v] */
} scheme_t;
typedef struct {
    unsigned char setting; /* SETTING_* (navigation.c): the bound row's click/text code */
    signed char def;       /* stored value not in values -> def; -1 none */
    signed char preset;    /* theme_preset writes it; -1 none */
    signed char keep;      /* a stored value theme_preset leaves (Light's Auto 2); -1 none */
    unsigned values;       /* bitmask of values the row takes; 0: all of setting_counts[setting] */
} option_t;
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
    unsigned char chevron_home, caps_mark, caps_home, caps_np, icons_mono, icons_badge_muted,
        icons_addon_mono, bold, np_album_op, np_state_op; /* flags 0/1; *_op 0/255 */
    short home_top, home_full_row, home_text_x, home_label_end[2] /* [0] split, [1] full */,
        set_top, np_bar, np_scrub;
    int *accent_slot;
    const char *accent_key;  /* &st.accent,"ACCENT" | &st.minimal_accent,"MINIMAL_ACCENT" */
    const scheme_t *schemes; /* [n_schemes], indexed by accent value: each scheme's dark half */
    const scheme_t *light; /* [n_schemes] each scheme's light half; == schemes when the theme states
                              no final value (Minimal, Classic) */
    unsigned char n_schemes; /* ACCENT_N, then the theme's own (Nano: 10) */
    const option_t *opts;    /* Appearance rows after Theme, in order */
    unsigned char n_opts;
    unsigned undeclared;     /* SETTING_* bits of the theme options it does not declare */
    unsigned char np_layout; /* NP_* */
    cf_style_t cf;
    unsigned char art_square; /* art takes Corners: Square */
    unsigned batt_fill;       /* 0xRRGGBB under the battery's outline; 0: the outline's own */
} theme_t;
const theme_t *theme(void);   /* accent(); return &themes[st.theme]; */
const scheme_t *scheme(void); /* theme()'s scheme accent(), in the half Light Mode picks */
int client_h(void); /* the window under the bar of the set theme_boot wrote (navigation.c) */
void paint_stops(void *canvas, int x, int y, int w, int h, const stops_t *s); /* navigation.c */
#endif
