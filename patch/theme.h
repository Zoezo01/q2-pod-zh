#ifndef Q2_THEME_H
#define Q2_THEME_H
/* The built-in themes' shape (docs/internals.md#themes); the table is navigation.c's themes[]. */

enum { PAINT_SELECTION, PAINTER_N }; /* theme_t.painter[] index */
enum { SEL_CARD, SEL_BAR };          /* painter[PAINT_SELECTION] (Minimal card, Classic bar) */
enum { SCHEME_CARD = 1, SCHEME_RAW_RED = 2 }; /* scheme_t.flags (Ivory in Minimal; Crimson) */
enum { NP_IPOD, NP_CENTRED };                 /* theme_t.np_layout: np_pages[] index */
typedef struct {
    const unsigned *tones; /* its ACCENTS row: accents[v] (top, bottom, light, red, highlight) */
    unsigned char flags;   /* SCHEME_* */
    /* 0xRRGGBB; 0: role unused (ponytail: no black role) */
    unsigned fill, chevron, muted, bar_light;
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
    const char *name; /* "Minimal": the Theme row's "Theme: %s" */
    unsigned char painter[PAINTER_N];
    unsigned char chevron_home, caps_mark, caps_home, caps_np, icons_mono, icons_badge_muted,
        icons_addon_mono, bold, np_album_op, np_state_op; /* flags 0/1; *_op 0/255 */
    short home_top, home_full_row, home_text_x, home_label_end[2] /* [0] split, [1] full */,
        set_top, np_bar, np_scrub;
    int *accent_slot;
    const char *accent_key;  /* &st.accent,"ACCENT" | &st.minimal_accent,"MINIMAL_ACCENT" */
    const scheme_t *schemes; /* [ACCENT_N], indexed by accent value */
    const option_t *opts;    /* Appearance rows after Theme, in order */
    unsigned char n_opts;
    unsigned char np_layout; /* NP_* */
    cf_style_t cf;
    short bar_h;              /* the status bar's height; client_h() is the window's under it */
    unsigned char art_square; /* art takes Corners: Square */
    unsigned batt_fill;       /* 0xRRGGBB under the battery's outline; 0: the outline's own */
} theme_t;
const theme_t *theme(void);   /* accent(); return &themes[st.theme]; */
const scheme_t *scheme(void); /* &theme()->schemes[accent()] */
static inline int client_h(void) { return 320 - theme()->bar_h; } /* the window under the bar */
#endif
