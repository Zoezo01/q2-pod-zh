#ifndef Q2_THEME_H
#define Q2_THEME_H
/* The built-in themes' shape (docs/internals.md#themes); the table is navigation.c's themes[]. */

enum {
    PAINT_SELECTION,
    PAINT_BAR,
    PAINT_BATTERY,
    PAINT_SCRUB,
    PAINT_VOLUME,
    PAINT_CONFIRM,
    PAINTER_N
}; /* theme_t.painter[] */
enum { VOL_STOCK, VOL_CLASSIC_LIGHT }; /* painter[PAINT_VOLUME]: the volume, slider pages, rating */
/* painter[PAINT_CONFIRM]: the confirm control, hold menu, toast and small dialogs */
enum { CONFIRM_STOCK, CONFIRM_CLASSIC_LIGHT };
enum { SCRUB_STOCK, SCRUB_ROWS }; /* painter[PAINT_SCRUB]: Now Playing's bar (Classic: its rows) */
enum { NP_ROWS = 31 };            /* theme_t.np_rows[][]: the rows painter's most rows */
enum { SEL_CARD, SEL_STOPS };     /* painter[PAINT_SELECTION] (Minimal card, Classic stops) */
enum { BAR_FLAT, BAR_GLOSS };     /* painter[PAINT_BAR]: the status bar's fill (Classic gloss) */
enum {
    BATT_STOCK,
    BATT_CLASSIC_LIGHT
}; /* painter[PAINT_BATTERY]: the bar's battery and status marks */
enum { SCHEME_CARD = 1, SCHEME_RAW_RED = 2 };      /* scheme_t.flags (Ivory in Minimal; Crimson) */
enum { SCHEME_DERIVE, SCHEME_LIGHT, SCHEME_DARK }; /* scheme_t.mode */
enum { NP_IPOD, NP_CENTRED, NP_CLASSIC_LIGHT, NP_N }; /* theme_t.np_layout: np_pages[] index */
enum {
    FONT_DEFAULT,
    FONT_BOLD,
    FONT_CLASSIC_LIGHT
}; /* theme_t.font[]: stock's, bold.ttf, classic-light.ttf */
/* theme_t.font[] index: list title, list second line, bar title, Now Playing title and Home rows, a
 * two-line row's title, Now Playing's artist and album (a theme stating them has its title at the
 * two-line row's) */
enum { ROLE_TITLE, ROLE_META, ROLE_BAR, ROLE_NP, ROLE_TALL, ROLE_NP_META, ROLE_INFO, ROLE_N };
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
    /* coverflow.shelf (px, brightness of 256; tilt: degrees, horizon: % down the cover, third: the
     * third cover out stays, swing: a step's depth follows the turn and the near edge eases to just
     * past its rest place, lift: px nearer mid-way, view_h: the frame's rows, art_persp: % of the
     * plane's own spacing of a cover's art, 0 even), coverflow.caption: offsets.inc's CF_* */
    short top, x1, xs, z1, zs, eye, bright1, bright2, reflect, reflect_top, stride, radius;
    short tilt, horizon, third, swing, lift, view_h, eye_turn, art_persp;
    /* coverflow.shelf, the thumb (docs/internals.md#coverflow-thumb; thumb 0: ticks and the step's
     * animator). Ticks are 200 / ticks_turn raw units; far_left, stop_left, back, aim_snap, lead_*
     * and row_from, _full, _flat, _edge are 1/100 album; others ms, ticks/s, albums/s, px or %. */
    short thumb, ticks_turn, ticks_album, fast_at, fast_full, top_speed, track, snap, spin;
    short far_left, detent, hold_ms, stop_ms, stop_left, rate_up, rate_down, back, dead;
    short contact_ms, aim_snap, lead_from, lead_full, lead_ms, row_shift, row_from, row_full;
    short row_shrink, row_flat, row_out, row_edge, row_width, row_key, row_reach;
    short name_px, artist_px, text_y, artist_y;
} cf_style_t;
enum { ELEM_ALIGN_LEFT, ELEM_ALIGN_CENTER, ELEM_ALIGN_RIGHT };
#define ELEM_UNSET (-32768) /* x, y, w, h not placed */
#define ELEM_KEEP 0xff /* visible, opacity, font, font_size, align left as the asset has them */
/* a stock widget a theme places (tools/theme.py ALIASES) */
typedef struct {
    unsigned char view, alias; /* VIEW_*, E_* (themes.inc) */
    short x, y, w, h;          /* the window's coordinates; ELEM_UNSET: not placed */
    unsigned char visible, opacity, font, font_size; /* ELEM_KEEP: unchanged; opacity 0..254 */
    unsigned color;      /* 0xffRRGGBB in stock's terms (Light Mode flips it); 0: unchanged */
    unsigned char align; /* ELEM_ALIGN_*; ELEM_KEEP */
} elem_t;
typedef struct {
    unsigned addr;
    unsigned char group;
} boot_site_t; /* one ipod.json immediate */
typedef struct {
    const char *name;
    unsigned char sets;
} ui_redirect_t; /* sets with <set>_<name>.bin */
enum {
    MOTION_PAGE,
    MOTION_PAYLOAD,
    MOTION_PLAYING,
    MOTION_DIALOG,
    MOTION_COVERFLOW,
    MOTION_HOME,   /* a page right over Home */
    MOTION_VOLUME, /* the row's push over Now Playing, not a window */
    MOTION_INPLACE,
    MOTION_SWAP,
    MOTION_STEP,
    MOTION_STEP_FAST,
    MOTION_COVERFLOW_STEP, /* as each step, its fast kind right after it */
    MOTION_COVERFLOW_STEP_FAST,
    MOTION_N
};
typedef struct {
    const char
        *hint; /* window kinds: the anim_hint a load or open writes; 0 leaves the window as is */
    unsigned short ms;    /* widget kinds: the glide's duration; 0 is an immediate move */
    unsigned char easing; /* widget kinds: AWTK easing_type_t */
} motion_t;
typedef struct {
    const char *name;   /* "Minimal": the Theme row's "Theme: %s" */
    unsigned char geom; /* second: THEME_GEOM_OFF (4) never moves */
    motion_t motion[MOTION_N];
    unsigned short recall_ms;   /* a remembered row's glide; 0: shown at once */
    unsigned char bump_px;      /* the wheel's bump at a list's end */
    unsigned short vol_hold_ms; /* the volume row's idle time over Now Playing; 0: stock's close */
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
    /* A list's scrollbar, a thumb alone (paint_scrollbar): width (0: stock's fading bar) and its
     * place from the list's right; the screen rows its ends stay in; its shortest length */
    short sb_w, sb_right, sb_top, sb_bottom, sb_min;
    unsigned sb_thumb[2]; /* final: [0] dark, [1] light */
    /* a row with art, on screen: the cover's x and its text's first and last columns; 0: stock's */
    short art_x, art_text_x, art_text_end;
    short row_text_end; /* a two-line row without art: its text's last column on screen */
    stops_t sep[2];     /* its last rows, one stop a row, final: [0] dark, [1] light; 0: none */
    /* the theme's classic_light_chevron's box on screen: a full-width list's, Split's; 0: stock's
     * list_into
     */
    short chevron_x, home_chevron_x, art_placeholder; /* stock's grey tiles (list_folder, list_song)
                                                         this size, centred, 0: as stock */
    unsigned char letter_raw; /* the fast-scroll letter's colours are final in both halves */
    short letter_px;          /* ... and its size, 0: LETTER_PX */
    short home_top, home_full_row, home_text_x, home_label_end[2] /* [0] split, [1] full */,
        set_top, np_bar, np_scrub;
    /* Now Playing's art: corner radius at 166 px; a tilted cover's corner rows on its widget
     * (cover_tilt, 1/10 px); its reflection's paper alpha (of 256, 0: none) until a widget row,
     * then rising to opaque at the widget's last */
    unsigned np_rows[4][NP_ROWS]; /* SCRUB_ROWS: 0xAARRGGBB; dark track, fill, light track, fill */
    short np_mark;                /* ... the scrub's diamond's half size, centred this far down */
    unsigned np_info_value[2];    /* the info page's values, final: [0] dark, [1] light */
    short np_art_radius, np_tilt_tl, np_tilt_tr, np_tilt_br, np_tilt_bl, np_reflect,
        np_reflect_from;
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
    /* Streaming's list in the pane (coverflow.c pane_service): a service's icon's place and size */
    short pane_svc_x, pane_svc_y, pane_svc_px;
    int *accent_slot;
    const char *accent_key;  /* &st.accent,"ACCENT" | &st.minimal_accent,"MINIMAL_ACCENT" */
    const scheme_t *schemes; /* [n_schemes], indexed by accent value: each scheme's dark half */
    const scheme_t *light; /* [n_schemes] each scheme's light half; == schemes when the theme states
                              no final value (Minimal, Classic) */
    unsigned char n_schemes; /* ACCENT_N, then the theme's own (Classic: 10) */
    const option_t *opts;    /* Appearance rows after Theme, in order */
    unsigned char n_opts;
    const elem_t *elems; /* the widgets it places, a container before its children */
    unsigned char n_elems;
    unsigned undeclared;     /* SETTING_* bits of the theme options it does not declare */
    unsigned char np_layout; /* NP_* */
    cf_style_t cf;
    unsigned char art_square; /* art takes Corners: Square */
    unsigned batt_fill;       /* 0xRRGGBB under the battery's outline; 0: the outline's own */
    /* page_list: a row, a row with a detail, the in-page title (0: the bar's) and the text's x; a
     * detail row's caption and detail boxes */
    short page_row, page_detail, page_title_h, page_x, page_cap_y, page_cap_h, page_det_y,
        page_det_h;
} theme_t;
const theme_t *theme(void);       /* accent(); return &themes[st.theme]; */
int theme_geom(const theme_t *T); /* navigation.c: the set T boots with its stored values */
const scheme_t *scheme(void);     /* theme()'s scheme accent(), in the half Light Mode picks */
int client_h(void); /* the window under the bar of the set theme_boot wrote (navigation.c) */
void paint_stops(void *canvas, int x, int y, int w, int h, const stops_t *s); /* navigation.c */
/* coverflow.c: the tilted cover's frame on w, and letting it go */
int cover_tilt(void *w, void *canvas, const char *file, unsigned paper, int bake);
void cover_tilt_close(void);
/* The rows painter: the track across w, the played part over its first fill_w px, a diamond at
 * fill_w when mark is set; final colours (Now Playing, Spotify). */
void paint_scrubber(void *canvas, int x, int y, int w, int h, int fill_w, int mark);
/* A page of the payload's own on the playing view: w takes that element's place and style.
 * 0 when the theme doesn't place it. */
enum {
    PLACE_COVER,
    PLACE_TITLE,
    PLACE_ARTIST,
    PLACE_ALBUM,
    PLACE_ELAPSED,
    PLACE_REMAIN,
    PLACE_BAR,
    PLACE_TILT,
    PLACE_POSITION,
    PLACE_HEARTS
};
int theme_place(int place, void *w, void *root);
/* A streaming page's tilted cover, and bar if it paints none itself (always full): page's tilt
 * shows file, redrawn when file, art or the half's paper changes. Each tick while the page is up.
 */
void stream_cover(void *page, void *tilt, void *bar, const char *file, unsigned art);
void stream_gone(void *tilt);
void np_image(void *w, const char *glyph); /* the theme's named glyph in this half, on w */
void theme_elements(int view, void *root); /* the theme's placed widgets on a window just loaded */
int theme_rect(int alias, int r[4]);
/* 0xRRGGBB to an opaque color_t, whose bytes are r, g, b, a. */
#define RGBA(c) (0xff000000u | ((c) & 255) << 16 | ((c) & 0xff00) | (c) >> 16)
#endif
