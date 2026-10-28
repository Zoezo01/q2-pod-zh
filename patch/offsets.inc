/* Stock V1.32 layout and ABI constants shared by patch/navigation.c and test/patch.py.
 * The test harness parses this file, so a field change cannot leave the mocks behind.
 * Fields marked "same slot" are class-specific views of one offset. */
#define I(p, o) (*(int *)((char *)(p) + (o)))
#define P(p, o) (*(void **)((char *)(p) + (o)))
#define FNV_SEED 2166136261u /* FNV-1a offset basis */
#define W_X 0x00
#define W_Y 0x04
#define W_W 0x08
#define W_H 0x0c
#define W_FOCUS 0x24            /* stock focused flag byte (tests) */
#define W_PARENT 0x48
#define IM_WIDGET 0x04 /* input_method_t's editing target, input_method_request @0x645758 */
#define IM_KEYBOARD                                                                                \
    0x0c /* the default input method's open keyboard window, 0 once it closes (@0x68bbec) */
#define W_EMITTER 0x60
#define W_CHILDREN_LAYOUT 0x68
#define CHILDREN_LAYOUT_VTABLE 0x10
#define DEFAULT_LAYOUT_X_MARGIN 0x18
#define DEFAULT_LAYOUT_SPACING 0x1a
#define DEFAULT_LAYOUT_VTABLE 0x926b20
#define EMIT_TYPE 0x08
#define EMIT_PENDING_REMOVE 0x15
#define EMIT_NEXT 0x20
#define ROW_HEIGHT 0x78         /* table_client row height */
#define ROW_INDEX 0x78          /* table_row logical index (same slot) */
#define SLIDE_INDEX 0x78        /* slide_menu current index (same slot) */
#define TABLE_ROWS 0x7c         /* table_client logical row count */
#define SELECTOR_INDEX 0x7c     /* text_selector selected option (same slot) */
#define VIEW_CONTENT_H 0x7c     /* scroll_view content height (same slot) */
#define SCROLL_X 0x80           /* scroll_view horizontal offset */
#define TABLE_TOP 0x80          /* table_client viewport top (same slot) */
#define SCROLL_Y 0x84           /* scroll_view viewport top */
#define VIEW_HORIZONTAL 0x90    /* scroll_view xslidable */
#define VIEW_VERTICAL 0x91      /* scroll_view yslidable */
#define VIEW_SNAP 0x92          /* scroll_view snap_to_page */
#define TABLE_ANIMATOR 0xd0
#define VIEW_ANIMATOR 0xe8
#define EVENT_TYPE 0x00
#define EVENT_KEY 0x18
#define EVENT_TIME 0x08 /* low word of the uint64 time_now_ms stamp */
#define EVENT_TARGET 0x10
#define EVENT_X 0x18           /* pointer_event_init @0x640298 */
#define EVENT_Y 0x1c
#define EVT_POINTER_DOWN 0x100 /* wm registers on_wm_tsdown_before_fun for this type @0x523ef0 */
#define EVT_POINTER_MOVE_BEFORE 0x102 /* wm move-before forwards this type to the down hook @0x523ed4 */
#define EVT_POINTER_UP_BEFORE 0x104
#define EVT_POINTER_ABORT 0x107
#define EVT_DESTROY 0x0c
#define EVT_KEY_DOWN 0x110        /* widget_on_keydown @0x6627c4 (0x111 is its long press) */
#define EVT_KEY_DOWN_BEFORE 0x112 /* wm keydown-before dispatch @0x523e6c */
#define EVT_KEY_UP 0x114          /* wm keyup dispatch @0x523eb8 */
#define EVT_KEY_UP_BEFORE 0x115   /* wm keyup-before dispatch @0x523e80 */
#define EVT_WINDOW_BACKGROUND 0x12a
#define EVT_WINDOW_CLOSE 0x12c
#define PULL_SEARCH_PX 48       /* device tuning: release distance from the initial touch */
#define PULL_CLAIM_PX 8
#define QUICK_EDGE_PX 31        /* stock on_wm_tsmove_fun @0x4e8d10 */
#define EVT_CLICK 0x10c
#define EVT_FOCUS 0x10e /* widget_set_focused_internal @0x65fdf4 */
#define EVT_BLUR 0x10f  /* widget_set_focused_internal @0x65fe40 */
#define EVT_IM_ACTION 0x13a /* the keyboard's OK, input_method_dispatch_action @0x645e20 */
#define EVT_SCROLL 0x14e /* stock scroll_view on_scroll @0x5efa40; table setter emits this too */
#define EVT_SCROLL_END 0x14f /* stock lists queue their visible covers on this (album page registers @0x4a43d0) */
#define VIEW_ON_SCROLL 0x100 /* scroll_view callback installed by list_view, called by touch @0x5efa5c */
#define CANVAS_X 0x00
#define CANVAS_Y 0x04
#define CANVAS_LCD 0x38
#define LCD_FILL_COLOR 0xbc
#define LCD_STROKE_COLOR 0xc0
#define LCD_TEXT_COLOR 0xb8     /* lcd_set_text_color @0x6474c4 */
#define CANVAS_ALIGN_V 0x30     /* canvas_set_text_align @0x63419c; middle = 1 */
#define CANVAS_ALIGN_H 0x34     /* center = 1 */
/* Selection outline tuning. RADIUS > 2 or the stock rounded path squares off; tests read these.
 * The shade uses the same stock dark surface as the fill at an alpha that keeps the outline
 * above 3:1 on pure white art (0x99 is the floor); on dark rows it matches the stock background
 * exactly and disappears. The line is translucent white at OUTLINE_ALPHA, so it reads as a soft
 * grey on the dark theme instead of a hard white edge; lower OUTLINE_ALPHA or SHADE_ALPHA
 * together and the contrast margin over bright art erodes. */
#define RADIUS 9
#define FILL_RGB 0x2b2b2b
#define FILL_ALPHA 0x40
#define SHADE_ALPHA 0xb3
#define OUTLINE_RGB 0xffffff
#define OUTLINE_ALPHA 0xb3
/* iPod accents, in the order of the Accent setting (IPOD/ACCENT in config.ini; 0 is the default):
 * { selection top, bottom, light tone, red tone, highlight }, 0xRRGGBB. Equal stops keep
 * selection flat on the Q2 panel; retain the fine top highlight. White text holds
 * 4.5:1 on every top. The light tone, the top lightened 12% toward white (Graphite keeps #6E6E6E),
 * is the Now Playing progress fill (3:1 or more on TRACK_COLOR). The highlight is the bar's one-pixel
 * top edge: the light tone, except Graphite's restrained #555555 over its flat bar. The red tone is what stock red #FF1448 becomes (switches, ticks, red text and
 * buttons): Graphite's is silver, so a lit switch reads against the grey off state; the others
 * reuse the light tone, 3:1 or more under white.
 * Crimson keeps every stock color. */
#define ACCENTS { 0x424242, 0x424242, 0x6e6e6e, 0xd8d8d8, 0x555555 }, /* Graphite */ \
                { 0xe8123f, 0xe8123f, 0xeb2f56, 0xff1448, 0xeb2f56 }, /* Crimson */ \
                { 0x13838d, 0x13838d, 0x30929b, 0x30929b, 0x30929b }, /* Tidal */ \
                { 0x8c732c, 0x8c732c, 0x9a8446, 0x9a8446, 0x9a8446 }, /* Champagne */ \
                { 0x2a6ad8, 0x2a6ad8, 0x447cdd, 0x447cdd, 0x447cdd }, /* Cobalt, the iPod's blue */ \
                { 0xc45400, 0xc45400, 0xcb681e, 0xcb681e, 0xcb681e }, /* Tangerine, the orange Q2 */ \
                { 0x903868, 0x903868, 0x9d4f7a, 0x9d4f7a, 0x9d4f7a }, /* Plum, the purple Q2 */ \
                { 0x424242, 0x424242, 0x6e6e6e, 0xd8d8d8, 0x555555 }, /* Ivory: Graphite, MINIMAL_FILL fills */ \
                { 0x647f34, 0x647f34, 0x778e4c, 0x778e4c, 0x778e4c }  /* Green, the green Q2 (#119: the shell's sage, darkened for white text); after Ivory so saved values keep */
#define CRIMSON 1
#define MINIMAL_ACCENT 7 /* Minimal theme only; its default */
/* The confirm pop-up's stock red discs (confirm_ok, confirm_cancel and their pressed images) take
 * this dark surface under every accent, Crimson included, through the same red-blend mapping: the
 * OK disc becomes CONFIRM_SURFACE, Cancel's lighter tint a mid grey, and the near-white glyphs stay
 * near white (14.2:1 and 5.6:1). The wheel's focus is the accent tile behind the disc, framed white. */
#define CONFIRM_SURFACE 0x2b2b2b
#define CONFIRM_IMAGE "confirm_"
/* Quick settings' active controls (drop_wifiopen, drop_btopen, drop_highgain, ...) are the same
 * red discs with white glyphs, so they take CONFIRM_SURFACE too. Their inactive discs stay stock
 * #444444 and disabled ones keep their grey glyphs. The brightness suns (DROPDOWN_SUN*) sit on
 * black, not a disc, so they keep the accent's red tone. */
#define DROPDOWN_IMAGE "drop_"
/* Stock's red button backgrounds (btn_enter, btn_red and their pressed images) sit under a white
 * label that is not in the image, so they take the light tone like an image with white on its red. */
#define BUTTON_IMAGE "btn_"
#define DROPDOWN_SUN "drop_ligh"
/* An active control's white glyph turns CONFIRM_SURFACE on an accent tone brighter than this
 * (perceived 0-255, 0.299 R + 0.587 G + 0.114 B): Graphite's silver; the others keep white. */
#define GLYPH_LIGHT_MAX 160
/* bitmap_t (docs/internals.md#accent): format u16 @0xe, 1 RGBA, 2 ABGR, 3 BGRA, 4 ARGB, 4 bytes a pixel
 * by bitmap_get_bpp_of_format; per format, the byte offsets of r, g, b and a. */
#define BITMAP_RGBA_AT { { 0, 1, 2, 3 }, { 3, 2, 1, 0 }, { 2, 1, 0, 3 }, { 1, 2, 3, 0 } }
#define BITMAP_PREMULTI 16 /* AWTK's BITMAP_FLAG_PREMULTI_ALPHA in bitmap_t's u16 flags @0xc */
#define STYLE_COLOR_GRADIENT_RET 0x649ff0 /* style_get_color's bal style_get_gradient (0x649fe8) returns here */
#define STOCK_RED 0xff1448
#define RED_TOLERANCE 8 /* per channel, 0-255: how far a color may sit from a red/neutral blend */
/* iPod black status chrome, in both Themes; TRACK_COLOR is Now Playing's progress track. */
#define BAR_COLOR 0x000000
#define TRACK_COLOR 0x1c1c1c
/* iPod: the confirm pop-up's Cancel row (tools/ipod.py confirm_dialog); its prompt is centred above
 * it at CONFIRM_PROMPT_PX (confirm_prompt). */
#define CONFIRM_TOP 208
#define CONFIRM_PROMPT_PX 24
#define NP_TIMES_PX 14 /* Now Playing's times, and the volume text in their place */
#define NP_ART_RADIUS 12 /* Now Playing's art corners (paint_cover), as stock's play_defaultcover scaled to the art */
/* A radius-r corner's arc at row y (0 the outermost): how far in it starts, in 1/16 pixels. */
static inline int arc_out(int r, int y) {
    int d = 2 * (r - y) - 1, v = (4 * r * r - d * d) * 64, s = 0;
    while ((s + 1) * (s + 1) <= v) ++s;
    return 16 * r - s;
}
#define PLACEHOLDER (IPOD ? "default_album_flow" : "default_album_big") /* Cover Flow's and Spotify's missing art */
#define VOL_POLL_MS 50 /* how often the volume indicator checks for a change */
/* The volume panel off Now Playing (vol_paint): exactly over Quick Settings' brightness row
 * (tools/ipod.py checks it), its rounded corners clear of the 80px glass corners. */
#define VOL_PANEL_X 26
#define VOL_PANEL_Y 256
#define VOL_PANEL_H 48
#define VOL_PANEL_PAD 20
#define VOL_PANEL_BAR 10
#define VOL_PANEL_TEXT 52
#define VOL_PANEL_PX 20 /* the panel's number */
#define VOL_PANEL_TRACK 0x5a5a5a
/* The centred 16px clock and battery clear the rounded glass; widths are checked with the stock font. */
#define CLOCK_EDGE 57
#define STATUS_GAP 4
#define BATT_PCT_W 42
#define BATT_PCT_PX 16
#define BATT_BODY_W 16
#define BATT_BODY_H 11
#define BATT_INSIDE_W 28
#define BATT_INSIDE_H 15
#define BATT_INSIDE_TEXT_DY 1
#define BATT_INSIDE_PX 13
#define BATT_NUB_W 2
#define BATT_NUB_H 5
#define BATT_PX 16
#define BATT_GAP 2
/* Icon + Percent: BATT_PX digits' ink (rows 8-20 of the 30 px bar, line box centred) sits a row
 * above the centred BATT_BODY_H icon (rows 10-20), so the icon rises BATT_PCT_DY to share its centre. */
#define BATT_PCT_DY 1
#define BATT_CHARGE_RGB 0x34c759
#define BATT_LOW_RGB 0xff1448
#define BATT_ROOM 94
/* Plain Bluetooth ink and codec reaches from the right edge of their stock canvases. */
#define BT_REACH 10
#define BT_CODECS { "bar_sbc", "bar_aac", "bar_aptx", "bar_aptxhd", "bar_ldac" }
#define BT_CODEC_REACH { 22, 12, 27, 41, 36 }
#define BT_GLYPH "bar_btcon" /* the white Bluetooth glyph; stock's bar_bt is the grey one */
/* A new codec badge shows CODEC_MS, then fades out and the glyph in, CODEC_STEPS steps of
 * CODEC_STEP_MS each way. */
#define CODEC_MS 1000
#define CODEC_STEP_MS 25
#define CODEC_STEPS 6
/* iPod: the full-screen art (stock's bigcover_page) shows the song name BC_MS, then it and its shade
 * fade out in BC_STEPS steps, BC_STEP_MS apart. */
#define BC_MS 3000
#define BC_STEP_MS 50
#define BC_STEPS 8
/* iPod drill rows: the stock list_into image sits where stock rows put their own img_into, its left
 * edge CHEVRON_W from the row's right edge (8 px margin + 50 px image), so every `>` lines up. */
#define CHEVRON_W 58
/* iPod Home: Split gives the menu half the screen (HOME_SPLIT_W) in both themes, the art the rest right of it;
 * Minimal's Full rows are full width. Labels end HOME_LABEL_END from the row's edge. The selection
 * dot (HOME_DOT px) sits at HOME_DOT_X, clear of the bottom row's corner, HOME_DOT_GAP before the
 * labels (HOME_TEXT_X in tools/ipod.py). */
#define HOME_FULL_ROW 375
#define HOME_LABEL_END 33
#define HOME_SPLIT_LABEL_END 8 /* Split: the art, not a corner, is beside the labels */
#define HOME_DOT_X 25
#define HOME_DOT 6
#define HOME_DOT_GAP 8
/* iPod Theme: Classic (IPOD/THEME 1, docs/ipod.md#display-settings) puts the pre-1.0.1 look back
 * over the Minimal assets at runtime. In Home's Full the rows end at HOME_CLASSIC_ROW, so the
 * chevron's glyph (x 20 to 30 of list_into) sits close to the panel edge without touching the
 * glass on the bottom row. Labels start at HOME_CLASSIC_TEXT_X and leave room for the glyph.
 * The progress bar is an NP_BAR_CLASSIC capsule
 * (Minimal's NP_BAR_MINIMAL line). Minimal's settings icons are grey and list_into
 * MINIMAL_CHEVRON under every accent; its own accent (MINIMAL_ACCENT) fills with MINIMAL_FILL. */
#define HOME_SPLIT_W 187
#define HOME_CLASSIC_ROW 369
#define HOME_TOP 8 /* Minimal's decorative inset; Classic starts below the status bar. */
#define HOME_CHEVRON_SHIFT 15
#define HOME_FULL_CHEVRON_SHIFT 10
#define HOME_CLASSIC_TEXT_X 33
#define HOME_CLASSIC_LABEL_END (CHEVRON_W - 10)
#define NP_BAR_CLASSIC 8
#define NP_BAR_MINIMAL 4
#define MINIMAL_FILL 0xeeeeec
#define MINIMAL_CHEVRON 0x777777
/* iPod Light Mode (IPOD/LIGHT 1, docs/ipod.md#light-mode): every neutral colour (channels within
 * LIGHT_SPREAD) flips per channel, black to LIGHT_PAPER and white to LIGHT_INK; hues are kept.
 * Accents keep their colours, and ink on a coloured selection stays white. A theme image without
 * colour flips too when it is mostly glyph: under LIGHT_SURFACE (1/n of its area) opaque and darker
 * than LIGHT_GLYPH, so a grey or dark disc keeps its white glyph. */
#define LIST_INTO_WHITE "file:///release/assets/default/raw/images/xx/list_into.png"
#define LIGHT_PAPER 0xf4
#define LIGHT_INK 0x16
#define LIGHT_SPREAD 16
#define LIGHT_CHROMA 40
#define LIGHT_GLYPH 0xa0
#define LIGHT_SURFACE 4
#define LIGHT_BAR 0xdedede      /* Classic's status bar in Light Mode, as an iPod's, flat */
/* Minimal's small capitals are SUDO_TRACK px apart (draw_spaced); secondary text is SUDO_MUTED. */
#define SUDO_TRACK 2
#define SUDO_MUTED 0x8c8c8c
#define HOME_CAPS_PX 18 /* Minimal's Home labels */
#define MARK_PX 14 /* and the status bar's idle Q2 */
/* Minimal's selection is a card SEL_INSET px in from each side of the row, corners SEL_RADIUS. */
#define SEL_INSET 6
#define SEL_RADIUS 6
#define TILE_INSET 2 /* album cards stay inside their tile on all four sides */
#define TILE_CLEARANCE 4 /* artwork and labels sit this far inside the card */
#define EDIT_OK_PX 18 /* the date, time and sleep timer pages' OK label, on that card */
/* Minimal's Now Playing album is NP_CAPS_PX capitals, NP_CAPS_INSET in from its label's edge as
 * stock hscroll_label insets the title and artist (margin_left, default 2, 0x5e8378). */
#define NP_CAPS_PX 15
#define NP_CAPS_INSET 2
#define PAGE_SLIDE_MS 120
#define NP_ARTIST_RGB 0xcccccc
/* Now Playing's text (local, Spotify and Radio): the title over the artist and album, the album
 * lighter than the other grey text so it reads at a glance. */
#define NP_TITLE_PX 22
#define NP_META_PX 18
#define NP_ALBUM_RGB 0xbbbbbb
/* iPod Now Playing's lyrics (lyrics.c, docs/ipod.md#lyrics): a line of one row is LYRIC_SLOT high,
 * its text and the gap to the next, a longer one more; the first after a blank LYRIC_SLOT
 * (tools/ipod.py's list layout) so it can sit in the middle as the playing line does; text LYRIC_X
 * from the left, as the art. The sung part LYRIC_LIT, the rest of the playing line LYRIC_DIM, the
 * other lines LYRIC_REST. */
#define LYRIC_SLOT 59
#define LYRIC_X 16
#define LYRIC_LIT 0xffffff
#define LYRIC_DIM 0x808080
#define LYRIC_REST 0x5f5f5f
/* Spotify's and Internet Radio's Now Playing layout (tools/ipod.py NP_*, which checks they match):
 * the art and the text 16px from the sides, 12 apart, the bar and times clear of the glass's
 * corners. */
#define SPOT_ART_X 16
/* iPod: Now Playing's art top, clear of the page dots (tools/ipod.py NP_ART_Y) */
#define NP_ART_TOP 42
#define SPOT_ART_Y (IPOD ? NP_ART_TOP : 50)
#define SPOT_ART_PX 166
/* iPod: the 50px heart/play-mode frame sits this far below the art's bottom edge, so the centred
 * 28px glyph (4px clear at its foot) ends level with the art (tools/ipod.py NP_ICON_PAD) */
#define NP_ICON_PAD 15
#define SPOT_TEXT_X 194
#define SPOT_TEXT_W 165
#define SPOT_BAR_X 21
#define SPOT_BAR_Y 251
#define SPOT_BAR_W 333
#define SPOT_BAR_H 8 /* the bar's view; iPod's Minimal paints its 4px line at the top (np_bar_h) */
#define SPOT_TIMES_Y 265
#define SPOT_TIME_X 46
/* iPod settings rows (docs/ipod.md#settings): Minimal starts SET_TOP below the status bar, Classic
 * flush, and holds SET_ROWS complete SET_ROW rows. Stock builds each row as a list_item holding a
 * SET_STOCK_W x SET_STOCK_BODY button at x SET_STOCK_X with a SET_STOCK_ICON icon, a label and a
 * 50px trailing image (chevron, tick or switch); items made SET_STOCK_ROW high are normalised to
 * SET_ROW. The icon shrinks to SET_ICON at SET_ICON_X, the text starts SET_GAP after it (or at
 * SET_TEXT_X without one) and trailing images end SET_EDGE from the right edge; test/build.py
 * and test/patch.py check them against the rounded glass in the first and last visible rows. */
#define SET_ROW 68
#define SET_TOP 8
#define SET_ROWS 4
#define SET_ICON 40
#define SET_ICON_X 28
#define SET_GAP 12
#define SET_TEXT_X 20
#define SET_EDGE 24
#define SET_STOCK_ROW 78
#define SET_STOCK_BODY 70
#define SET_STOCK_X 20
#define SET_STOCK_W 335
#define SET_STOCK_ICON 52
#define SET_STOCK_TRAIL 276   /* the usual trailing image x in the stock button */
#define SET_RIGHT_SIDE 150    /* stock children starting here keep to the right edge */
#define LIST_DEFAULT_ITEM_HEIGHT 0x7c  /* list_view default_item_height */
#define LIST_VIEW_LAYOUT 0x5e9eb4      /* children_layouter_list_view's layout */
#define LIST_VIEW_LAYOUT_SLOT 0x926930 /* its vtable slot (vtable 0x926928 + 8) */
#define IMAGE_DRAW_SCALE_DOWN 5        /* "scale_down" in the stock draw type table @0x9272c0 */
#define IMAGE_DRAW_CENTER 1            /* "center", the same table */
/* iPod fast-scroll letter: the selected row's first character in white, LETTER_PX high, on a
 * LETTER_BOX square of the stock dark surface at LETTER_ALPHA, centred over the list. */
#define LETTER_BOX 80
#define LETTER_RADIUS 12
#define LETTER_ALPHA 0xd9
#define LETTER_PX 60
#define LETTER_MS 400
/* iPod Now Playing scrub: SCRUB_STEP seconds per detent times the list ramp, previewed until
 * centre or Return commits it, or SCRUB_MS without input commits it and puts the wheel back on
 * volume. */
#define SCRUB_STEP 5
#define DOUBLE_CLICK_MS 250 /* a second centre press within this is a double press */
#define WAKE_MS 500 /* Wake: Double press, the screen-off double press; looser, done blind in a hand */
#define SCRUB_MS 3000
/* With the screen off the UI loop idles this long a turn instead of stock's 8 ms, about 10 turns a
 * second; screen-off keys (volume, play/pause, wake) wait at most this much longer. */
#define SCREEN_OFF_SLEEP_MS 100
/* Stock's player status sync (5 a second) and Tidal's stream poll sleep at least this long a turn
 * while ringnav_sleep finds the Q2 dark and quiet (ringnav_dark_usleep), its cover pollers (20 and
 * 10) whenever it is dark (ringnav_dark_cover_usleep); DARK_POLLS in tools/build.py. */
#define DARK_POLL_US 1000000
/* Screen off, nothing playing (media_playing) and the DAC already off (check_dacoff_state, about 31
 * s after playback stops; g_dacoff_time -1): the turn idles DARK_IDLE_SLEEP_MS, about 4 turns a
 * second, so a Q2 left dark without Standby stops waking the CPU for nothing. The first key waits
 * at most that; Wake's second press is timed at the screen-off pace. */
#define DARK_IDLE_SLEEP_MS 250
/* Bluetooth absolute volume is read back this often with the screen off and no volume limit to
 * hold, or after the headset refused it (bt_volume_poll); 400 ms otherwise. A headset's own volume
 * keys show on the Q2 that late. */
#define BT_VOLUME_SLOW_MS 2000
#define BT_VOLUME_LIMIT_MS 1000
/* Screen on, once IDLE_MS pass without input or a window animation, each turn idles IDLE_SLEEP_MS
 * (about 30 passes a second; the first input after waits at most that). */
#define IDLE_MS 2000
#define IDLE_SLEEP_MS 25
/* Charge limit: the charger stops at CHARGE_STOP percent and starts again at CHARGE_RESUME,
 * checked every CHARGE_POLL_MS. */
#define CHARGE_STOP 80
#define CHARGE_RESUME 75
#define CHARGE_POLL_MS 5000
/* Standby limit (docs/internals.md#standby-limit): its three choices after Off, in hours of one
 * Standby, after which the Q2 powers off. */
#define STANDBY_LIMIT_1 1
#define STANDBY_LIMIT_2 8
#define STANDBY_LIMIT_3 24
/* The Wi-Fi signal (ringnav_wifi_signal) is read from the driver again at most every
 * WIFI_SIGNAL_MS; its two pollers each ask about once a second. */
#define WIFI_SIGNAL_MS 1000
/* Resume (docs/internals.md#resume): tracks of Q2POD RESUMEMIN minutes or longer (crossfade.c
 * resume_min) keep their place in the RESUME_SLOTS most recent; a place within RESUME_EDGE_S of
 * either end is forgotten. Saved on a track change, a pause and every RESUME_SAVE_S of play;
 * restored if the track has played less than RESUME_START_S when first seen playing. */
#define RESUME_SLOTS 512
#define RESUME_EDGE_S 30
#define RESUME_SAVE_S 60
#define RESUME_START_S 5
/* Play counts (docs/internals.md#play-counts): a track over LISTEN_MIN_S counts once half of it,
 * or LISTEN_MAX_S, has been heard; PLAYS_SLOTS counts are kept, the lowest replaced when full, and
 * Most Played lists the top PLAYS_TOP. */
#define LISTEN_MIN_S 30
#define LISTEN_MAX_S 240
#define PLAYS_SLOTS 512
#define PLAYS_TOP 100
/* Ratings (docs/internals.md#ratings): RATE_SLOTS songs keep 1-4 hearts (5 is My Fav); holding
 * Now Playing's heart RATE_HOLD_MS opens the picker. */
#define RATE_SLOTS 4096
#define RATE_HOLD_MS 500
/* Long Songs (docs/internals.md#long-songs): songs longer than this. */
#define LONG_SONG_S 600
/* End bump and wrap, audited row lists only (see ring_list in patch/navigation.c). A detent past an
 * end nudges the outline; further detents stop there while the wheel keeps turning, and the first
 * same-direction detent after a pause of EDGE_PAUSE_MS moves to the other end of the same list. */
#define BUMP_PX 6
#define BUMP_MS 120
#define EDGE_PAUSE_MS 300 /* well above WHEEL_RUN_MS, so a spin never counts as a pause */
#define BAR_VALUE 0x7c /* scroll_bar native value, maintained by list/table scroll callbacks */
#define KEY_CENTER 218
#define KEY_PREV 172
#define KEY_NEXT 173
#define KEY_PLAY 171
#define KEY_RETURN 170 /* on_common_keyup @0x4e8124 */
#define KEY_BACK_BTN 222 /* the previous and next buttons: on_wm_keyup_fun, prev_quick_click */
#define KEY_FWD_BTN 223
#define KEY_LOCKOUT 0xa37c89 /* stock: a centre key sets 8, on_wm_timer_setup_state counts it down per 100 ms */
/* Stock drops wheel ticks while KEY_LOCKOUT is set, 800 ms after Centre, so a press that brushes
 * the wheel cannot scroll; only its first LOCKOUT_KEEP x 100 ms are kept (ringnav). */
#define LOCKOUT_KEEP 2
/* Wheel ticks during a page's slide or before its list loads wait up to HOLD_MS, at most HOLD_MAX
 * of them, then play into the page one per HOLD_POLL_MS, so a turn begun on Back or Select carries
 * on instead of being lost. */
#define HOLD_MS 1200
#define HOLD_MAX 8
#define HOLD_POLL_MS 16
/* The wheel (playback.c wheel_sample): knob units per tick at 50% Wheel sensitivity (a seventh of
 * a turn), 200 a turn, and stock's poster encoderknob_thread_run+0x150, which dispatches the
 * thread info's key event. */
#define WHEEL_TICK_UNITS 28
#define WHEEL_POST 0x6257f0
#define WHEEL_INFO_EVENT 0x20 /* event type: 0x110 key down, 0x114 key up */
#define WHEEL_INFO_KEY 0x38   /* the event's key (EVENT_KEY) */
/* The poster's target is the info's main loop, whose input queue (stock event_queue_t: u16 r, w,
 * full, capacity) holds 20 events and drops its oldest when full. A tick is two events, so a fast
 * turn while the UI is busy evicted a touch's release and left the pointer pressed, which stops the
 * wheel until the next tap. wheel_post drops ticks that would leave under WHEEL_QUEUE_FREE slots.
 */
#define WHEEL_INFO_LOOP 0x10
#define LOOP_QUEUE 0x38
#define WHEEL_QUEUE_FREE 8
/* With the screen off (the wheel is the volume), WHEEL_DARK_GUARD units of one touch's travel one
 * way (100, half a turn, whatever the sensitivity) tick nothing; the wheel then ticks until it has
 * been still WHEEL_DARK_IDLE_MS, which also restarts an unfinished guard. Calibration knobs. */
#define WHEEL_DARK_GUARD 100
#define WHEEL_DARK_IDLE_MS 1500
#define BOOT_KEY_GUARD 0xa37c8a /* no symbol: set after a Standby resume, on_wm_keyup_fun drops the next centre release */
#define RETURN_RELEASE_LATCH 0xa37c8b /* stock long Return arms it; the key-up filter consumes it */

/* slide_menu_scroll_to @0x5f3400 and completion @0x5f3654; animator
 * start @0x665ab0, time_elapse @0x6660d4, scroll_set_params @0x682124. */
#define SLIDE_SPACER 0x84
#define SLIDE_OFFSET 0x94
#define SLIDE_ANIMATOR 0xc8
#define ANIM_ELAPSED 0x10
#define ANIM_START_TIME 0x18
#define ANIM_DURATION 0x20
#define ANIM_X_TO 0x60
#define SLIDE_EASING 8          /* stock scroll_to easing */
#define EVT_ANIM_END 0x125

/* Play/Pause hold queue menu (docs/internals.md). AWTK input_device_status lives at
 * wm+0x98 (window_manager_get_input_device_status @0x66e734): 16 key records of 0x18 bytes from
 * +0x30, each {key, long fired, u64 press time @8, aborted @0x10}. */
#define EVT_KEY_LONG 0x111
#define INPUT_KEYS 0x30
#define INPUT_KEY_SIZE 0x18
#define INPUT_KEY_COUNT 16
#define INPUT_KEY_TIME 0x08
/* stSongInfo rows of p_deque_showlist and the mcl queue. */
#define REC_ID 0x00             /* -1/-2: unknown album/artist */
#define REC_TIDAL_ID 0x04       /* a Tidal queue row's song id (get_tidal_trackurl 0x48323c) */
#define REC_NAME 0x08           /* the file name; REC_TITLE is the tag's, empty when untagged */
#define REC_PATH 0x0c
#define REC_TITLE 0x10
#define REC_ALBUM 0x14
#define REC_ARTIST 0x18
#define REC_ALBUM_ARTIST 0x24   /* albumsonger; empty or null in folder-queue records */
#define REC_COVER 0x28          /* an online row's cover, a Tidal row's a jpg in /tmp/tidal (player_get_id3info 0x5149d8) */
#define REC_TYPE 0x34           /* 8 song, 4 directory */
#define REC_TRACK 0x3c          /* the tag's track and disc numbers, 0 when untagged */
#define REC_DISC 0x40
#define REC_CUE_START 0x48      /* a CUE track's start and end seconds, 0 otherwise */
#define REC_CUE_END 0x4c
/* g_play_id3_info, as player_get_id3info (0x5146bc) parses the playing file: its path, then tags. */
#define ID3_ALBUM 0x404
#define ID3_ARTIST 0x504
#define ID3_TITLE 0x684
#define MUSIC_INFO_ALBUM_ARTIST 0x580 /* toolsGetMusicInfo's info (ID3_ALBUM's base): abmartist, the scan's albumsonger */
#define ID3_ALBUM_ARTIST (ID3_ALBUM + MUSIC_INFO_ALBUM_ARTIST) /* a CUE track's, from its file (ringnav_id3_path) */
#define CLASS_ALBUMS 0xf003     /* all albums: rows open getMusicByAlbum (load_localclass_list 0xff10) */
/* localmusic/playlist_page's add mode (g_play_info + 0x7894): non-zero while it is Add to Playlist;
 * 0 when browsing the Playlists list, whose rows' trailing dots open playlistsmore_page. */
#define PLAYLIST_ADD_MODE 0xa27894
#define MUSIC_WHERE "fileurl not like '/mnt/mmc/audiobooks/%' and fileurl not like '/mnt/mmc/podcasts/%' and fileurl not like '%.m4b'"
/* Tags may store a full date (20220510 or 2022-05-10); compare the year before aggregating. */
#define ALBUM_YEAR "max(case when cast(year as text) glob '[1-9][0-9][0-9][0-9]*' then cast(substr(year,1,4) as integer) else 0 end)"
/* One row per album, as album_cmp tells them apart (coverflow.c; navigation.c's Era). */
#define ALBUM_GROUP " group by album COLLATE NOCASE," \
    "ifnull(albumsonger,'') COLLATE NOCASE,case ifnull(albumsonger,'') when '' then rtrim(fileurl,replace(fileurl,'/','')) end"
#define CLASS_ARTIST_ALBUMS 0xff01 /* an artist's albums (load_localclass_list 0xff11) */
#define ALBUMINFO_ROW 0xa27200  /* the albums row localmusic_albuminfo_page_init opened (0x4a7ab4) */
/* Static mcl queue state (mclLoadPlayList @0x5aa8d4, mclNextSong @0x5adbbc). */
#define MCL_POOL 0xa3bdb0       /* deque_t * of int: unplayed shuffle indices */
/* The playing track's lyrics (mclGetLyricSize @0x5aac20): a deque_t * of stLyricInfo { int time;
 * char *text; }, whose copy duplicates the text, and the mutex every reader and the loader hold. */
#define MCL_LYRICS 0xa3bdac
#define MCL_LYRIC_LOCK 0xa3bdb8
/* demo's GOT entry for taglib_file_new, libtag_c's own address once a call has bound it, and the
 * pinned libtag_c.so.2.0.1's (build.py TAG_C_SHA) offsets of it and the property calls demo lacks. */
#define TAGLIB_NEW_GOT 0xa2662c
#define TAGLIB_FILE_NEW 0x98e0
#define TAGLIB_PROPERTY_GET 0xb4dc
#define TAGLIB_PROPERTY_FREE 0xb700
/* Now Playing's ⋯ page (playermore_page_init @0x528c14): its 8 rows' kinds by position, which the
 * row click (0x528a70) reads at the button name's atoi, and Download Cover & Lyrics' kind
 * (get_playmore_showinfo @0x501208: player_onlinesearch). */
#define PLAYMORE_ROWS 0x7728a0
#define PLAYMORE_COUNT 8
#define PLAYMORE_SEARCH 4
#define MCL_LASTPOS 0xa3be90    /* shuffle "previous" index, -1 none */
#define MCL_PREPOS 0xa3be94
#define MCL_PRELOAD 0xa3be98    /* 1 preloaded, 0 none, -1 closed for this track */
#define MCL_TYPE 0xa3bea0       /* queue classType: 1 folder, 0xfxxx local */
#define MCL_POS 0xa3bea4
#define MCL_MODE 0xa3bea8       /* 0 order, 1 repeat one, 2 shuffle, 3 repeat all */
#define MCL_JUMPFOLDER 0xa3be53 /* byte: Folder Skip, read only for class 1 queues */
#define MCL_GAPLESS 0xa3be54    /* byte: stock's Gapless setting (mclGetGaplessMode); mclAutoChange preloads only with it */
#define MCL_FD 0xa3beb4         /* player socket, -1 none */
#define INITCONFIG_MODE_RET 0x513420 /* player_initconfig's mclSetPlayMode call (jalr 0x513418) returns here */
/* mclUsbAudioSetVol (0x5ac574): the USB DAC's volume control, -1 before the first set, -2 when it
 * has none and hciplayer's soft volume (mcl-softvolflag) applies instead (0x5a8498 opens hw:1). */
#define USB_MIXER 0xa3be6c
/* Stock sortselect_dialog callbacks the menu replaces: both set g_sort_changeflag, which makes
 * allmusic reload and scroll to the top on return. */
#define SORTSELECT_KEYUP 0x49f3e0
#define SORTSELECT_CLOSE 0x49f3c0

/* Coverflow (docs/internals.md). slide_menu dispatches EVT_VALUE_CHANGED after its
 * index moves (0x5f320c). The stock scan thread handle (dialog_updatemusic_dialog_init) and its
 * done flag (wait_scan sets it) have no symbols: a scan runs while the handle is set and the
 * flag is clear. */
#define EVT_VALUE_CHANGED 0x0e
#define EVT_VALUE_CHANGING 0x0f
/* playset_bootvol_page_init (0x4b4a80) binds these, each with the window as context; Crossfade
 * re-dresses the page (crossfade.c) and unbinds them first. */
#define BOOTVOL_SWITCH 0x4b44d8   /* on_bootvol_switch_onclick: img_bootswitch's click */
#define BOOTVOL_DEC 0x4b4624      /* img_dec's click */
#define BOOTVOL_ADD 0x4b45a8      /* img_add's click */
#define BOOTVOL_CHANGING 0x4b48f4 /* slider_bootvol's EVT_VALUE_CHANGING */
#define BOOTVOL_CHANGED 0x4b4300  /* slider_bootvol's EVT_VALUE_CHANGED */
#define SCAN_THREAD 0xa271d8
#define SCAN_DONE 0xa271d4
/* Coverflow text: iPod keeps every label CF_EDGE from the sides, where the lowest track row's text
 * clears the bottom corners; both builds keep the captions there. Album names and artists scroll
 * within that. Under the covers (CF_TEXT_Y), the album name is CF_NAME_PX white and the artist
 * CF_ARTIST_PX in CF_GREY (stock secondary text) right under it. */
#define CF_EDGE 36
#define CF_NAME_PX 24
#define CF_NAME_H 32
#define CF_ARTIST_PX 20
#define CF_ARTIST_H 28
#define CF_GREY 0xffaaaaaa /* color_t #AAAAAA */
/* Coverflow depth (docs/internals.md#coverflow-depth): the covers are drawn in software into one
 * CF_VIEW_W x CF_VIEW_H frame, the top of the client area. The selected cover is ART_SIZE (160)
 * square with its top CF_TOP from the frame's; side covers turn CF_ANGLE (60) degrees inward (the
 * sine table in coverflow.c), sit CF_X1 px from the centre, then CF_XS apart, CF_Z1 px and then
 * CF_ZS more behind it, seen from CF_EYE px. Brightness (of 256) is 256 at the centre, CF_BRIGHT1
 * one cover out and CF_BRIGHT2 two out, reaching 0 three out. Each cover's reflection is
 * CF_REFLECT px high at native size, from CF_REFLECT_TOP (of 256) of its brightness down to black.
 * The wheel moves one album every CF_STRIDE px. Starting values, to be tuned together on the Q2. */
#define CF_VIEW_W 375
#define CF_VIEW_H 210
#define CF_CX 187 /* the centre line lies between screen columns 186 and 187 */
#define CF_TOP 13
#define CF_EYE 300
#define CF_X1 112
#define CF_XS 48
#define CF_Z1 90
#define CF_ZS 20
#define CF_BRIGHT1 192
#define CF_BRIGHT2 128
#define CF_REFLECT 24
#define CF_REFLECT_TOP (IPOD ? 48 : 64)
#define CF_STRIDE 110
#define CF_TEXT_Y 212 /* the album caption's top, under the frame; the artist follows */
/* Photos (patch/photos.c, docs/internals.md#photos): under a 48px title, PH_COLS columns of
 * PH_TILE_W x PH_TILE_H tiles from x PH_GRID_X, three whole rows in the 240px below it, each a
 * thumbnail of at most PH_THUMB_W x PH_THUMB_H centred in its tile, so the selection shows round
 * it. The viewer's screen-size copy fits PH_SHOT_W x PH_SHOT_H (the client area). PH_GREY marks a
 * thumbnail not made yet or a file that will not open. */
#define PH_COLS 4
#define PH_GRID_X 1
#define PH_TILE_W 93
#define PH_TILE_H 80
#define PH_THUMB_W 85
#define PH_THUMB_H 72
#define PH_SHOT_W 375
#define PH_SHOT_H 290
#define PH_GREY 0xff333333 /* color_t #333333 */
#define PHOTOS_MAX 500 /* ponytail: ringnav navigates at most MAX_ENTRIES (512) rows; page beyond */
/* PEQ editor (patch/peq_ui.c): on the main and band screens a PEQ_GRAPH_H view under the title
 * holds the response from PEQ_GRAPH_X to 375 - PEQ_GRAPH_X (20 Hz to 20 kHz, log), +-PEQ_GRAPH_DB at
 * its plot's top and bottom (rows PEQ_PLOT_TOP to PEQ_PLOT_BOTTOM), frequency labels under it. */
#define PEQ_GRAPH_H 96
#define PEQ_GRAPH_X 16
#define PEQ_GRAPH_DB 15
#define PEQ_PLOT_TOP 6
#define PEQ_PLOT_BOTTOM 78
/* iPod visualizer (patch/visualizer.c, docs/ipod.md#visualizer), Now Playing's fourth page. The
 * page shows the audio VIS_LATENCY_MS behind what hciplayer last handed the filter: its ao_alsa asks
 * for a 500 ms buffer in 16 periods (0x438a2c: buffer_time 500000 us) and refills it a period at a
 * time, so a block waits about 500 ms less half a period to a period; Bluetooth adds its own, so this
 * stays the knob for sync. VIS_FPS frames a second; spectrum bars
 * fall VIS_DECAY of full scale a second, their caps hold VIS_HOLD_MS then fall at VIS_GRAVITY full
 * scale a second squared. Levels span VIS_FLOOR_DB below full scale; the VU meters read 0 VU at
 * VIS_VU_REF_DB dBFS RMS and light their peak LED from VIS_LED_DB dBFS for VIS_HOLD_MS. A paint that
 * found the page unsettled (under a closing window, mid-slide) looks again VIS_RETRY_MS later. */
#define VIS_LATENCY_MS 480
#define VIS_RETRY_MS 250
#define VIS_FPS 25
#define VIS_BARS 32
#define VIS_DECAY 1.6f
#define VIS_HOLD_MS 400
#define VIS_GRAVITY 3.0f
#define VIS_FLOOR_DB 60
#define VIS_VU_REF_DB -14
#define VIS_LED_DB -1
