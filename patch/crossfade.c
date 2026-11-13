/* Crossfade (docs/internals.md#crossfade): Playback's row, its page and the fade hciplayer's
 * filter applies (peq_player.c). The page is stock's Boot volume page re-dressed: its slider runs
 * Off (0) and XF_MIN..XF_MAX seconds, so it needs no switch; the switch and Centre still turn it on
 * and off. Display's Wheel sensitivity row opens the same page (xf.ctx 1): 10-200% in tens, no
 * switch, the wheel a ten a tick and Centre back, as on Brightness (navigation.c edit_key). Audio
 * settings' Resume Songs row opens it too (xf.ctx 2): Off and RS_MIN..RS_MAX minutes in fives, the
 * same way, the length a music track needs to keep its place (navigation.c resume_poll). Display's
 * Night Shift row opens it as well (xf.ctx 3): the switch and Centre cycle Off, On and Auto, as
 * Light Mode's row does, and the slider runs NS_COOL to NS_WARM, warmer to the right, a NS_STEP a
 * tick (nightshift.c). Mono Audio's row lives here too (mono_sync). */
#include "offsets.inc"
#include "peq.h"
#include "playback.h"
#include "stock.h"

#define XF_MIN 1
#define XF_MAX 15
#define XF_DEFAULT 5
#define XF_ROW "label_xfade"     /* the Playback row's label, found again by name */
#define WHEEL_ROW "label_wheel"  /* and Display's Wheel sensitivity row's */
#define RS_ROW "label_resumemin" /* and Playback's Resume Songs row's */
#define NS_ROW "label_night"     /* and Display's Night Shift row's */
#define RS_MIN 20
#define RS_MAX 120
#define RS_DEFAULT 30

static struct {
    int read, on, seconds, done, key[8], stops, counted;
    int ctx; /* the page's: 0 Crossfade, 1 Wheel sensitivity, 2 Resume Songs, 3 Night Shift */
    int sliding, unsaved, setting; /* a finger on the slider; a value it moved; refresh's own set */
    int rs_read, rs_min;
    int mono_read, mono, swap_read, swap;
    volatile int *flag, *mono_flag, *swap_flag, *press_file, *mute_file;
    void *swap_label;
    void *mono_label; /* the Mono Audio row's, while Playback is open */
    void *page, *sw, *view, *slider, *dec, *add, *label;
    unsigned press, press_at;
} xf __attribute__((section(".scratch")));

/* Q2POD XFADE (0, 1) and XFADESEC (XF_MIN..XF_MAX) in config.ini, read once. */
static void settings(void) {
    if (xf.read) return;
    char v[256] = "";
    xf.on = config_value("Q2POD", "XFADE", 2, 0);
    toolsReadConfig("/mnt/data/config.ini", "Q2POD", "XFADESEC", v, "0");
    int n = atoi(v);
    xf.seconds = n >= XF_MIN && n <= XF_MAX ? n : XF_DEFAULT;
    xf.read = 1;
}

static void row_text(void *label) {
    char t[32] = "Crossfade: Off";
    if (xf.on) tk_snprintf(t, sizeof t, "Crossfade: %d s", xf.seconds);
    widget_set_text_utf8(label, t);
}

/* Playback's row (navigation.c ringnav_audioset makes it): the label, named for refresh(). */
void xfade_row(void *label) {
    settings();
    widget_set_name(label, XF_ROW);
    row_text(label);
}

static void wheel_text(void *label) {
    char t[32];
    tk_snprintf(t, sizeof t, "Wheel Sensitivity: %d%%", wheel_pct());
    widget_set_text_utf8(label, t);
}

/* Display's row (navigation.c ringnav_display makes it): the label, named for refresh(). */
void wheel_row(void *label) {
    widget_set_name(label, WHEEL_ROW);
    wheel_text(label);
}

/* Q2POD RESUMEMIN in config.ini, read once: 0 (Off) or RS_MIN..RS_MAX in fives, else RS_DEFAULT. */
int resume_min(void) {
    if (!xf.rs_read) {
        char v[256] = "";
        toolsReadConfig("/mnt/data/config.ini", "Q2POD", "RESUMEMIN", v, "30");
        int n = atoi(v);
        xf.rs_min = v[0] == '0' && !v[1]                     ? 0
                    : n >= RS_MIN && n <= RS_MAX && !(n % 5) ? n
                                                             : RS_DEFAULT;
        xf.rs_read = 1;
    }
    return xf.rs_min;
}

static void rs_text(void *label) {
    char t[32] = "Resume Songs: Off";
    if (resume_min()) tk_snprintf(t, sizeof t, "Resume Songs: %d min", resume_min());
    widget_set_text_utf8(label, t);
}

/* Playback's row (navigation.c ringnav_audioset makes it): the label, named for refresh(). */
void resume_row(void *label) {
    widget_set_name(label, RS_ROW);
    rs_text(label);
}

static void night_text(void *label) {
    char t[32] = "Night Shift: Off";
    if (night_on() == 2)
        tk_snprintf(t, sizeof t, "Night Shift: Auto");
    else if (night_on())
        tk_snprintf(t, sizeof t, "Night Shift: %dK", night_kelvin());
    widget_set_text_utf8(label, t);
}

/* Display's row (navigation.c ringnav_display makes it): the label, named for refresh(). */
void night_row(void *label) {
    widget_set_name(label, NS_ROW);
    night_text(label);
}

/* An int shared with hciplayer's filter (and q2video): made whole under tmp and renamed, as the
 * visualizer's tap is, then mapped; 0 if it cannot be made yet, so the caller tries next pass.
 * Written, not ftruncate'd: that allocates no page, so a full tmpfs would SIGBUS the first store. */
static volatile int *shared_int(const char *tmp, const char *path) {
    void *p = (void *)-1;
    int zero = 0, fd = open(tmp, 0x302, 0644); /* O_RDWR | O_CREAT | O_TRUNC (MIPS) */
    if (fd < 0) return 0;
    if (write(fd, &zero, sizeof zero) == sizeof zero) {
        p = mmap(0, sizeof(int), 3, 1, fd, 0); /* PROT_READ | PROT_WRITE, MAP_SHARED */
        if (p != (void *)-1 && rename(tmp, path)) {
            munmap(p, sizeof(int));
            p = (void *)-1;
        }
    }
    close(fd);
    return p != (void *)-1 ? p : 0;
}

/* What waits for the DAC (docs/internals.md#wired-start) is opt in: a site missed keeps the old cut, a
 * stamp on a change nobody pressed for would lengthen its gap. Only a local queue's: DLNA and online
 * queues (0xf1, 0xf2) start tracks by themselves through the same calls. One int write per press. */
int press_natural __attribute__((section(".scratch")));
static int press_word(void) {
    unsigned type = *(volatile unsigned *)MCL_TYPE;
    if (!(type == 1 || (type & 0xf000) == 0xf000)) return 0;
    if (!xf.press_file) xf.press_file = shared_int(PRESS_FILE ".tmp", PRESS_FILE);
    return xf.press_file != 0;
}
void press_stamp(void) {
    if (!press_natural && press_word()) *xf.press_file = (int)now_ms() | 1; /* odd: never 0 */
}
/* mclStartPlayer's wired mute and sleep on a start from the idle player (0x5bf8c4, 0x5bf8dc). A natural start
 * skips both, so the old track is heard to its end, and leaves an even mark for ao_init, whose reopen mutes
 * after the drain, or which keeps the PCM open. Only ringnav_next(1) sets press_natural to 2: anything else,
 * or no word to leave the mark in, keeps stock's. */
static int start_quiet __attribute__((section(".scratch")));
int start_mute(int value) {
    start_quiet = press_natural == 2 && press_word();
    if (!start_quiet) {
        if (xf.press_file && !(*xf.press_file & 1)) *xf.press_file = 0; /* muted: an unspent mark isn't for this start */
        return mclSetMute(value);
    }
    int at = (int)now_ms() & ~1; /* even, and never 0 */
    *xf.press_file = at ? at : 2;
    return 0;
}
int start_sleep(unsigned us) {
    if (start_quiet) return start_quiet = 0;
    return usleep(us);
}
void mute_stamp(void) {
    if (!xf.mute_file) xf.mute_file = shared_int(MUTE_FILE ".tmp", MUTE_FILE);
    if (xf.mute_file) *xf.mute_file = (int)now_ms() | 1; /* odd: never 0 */
}
void press_forget(void) {
    if (xf.press_file) *xf.press_file = 0;
}

/* The PEQ filter into the chain, in bypass, for the visualizer's tap, a fade or Mono Audio: boot
 * leaves it out with the EQ off, and any peq_stock_eq since (the EQ switched) has put it in. Only
 * while something plays (1 stopped), so a call while stopped is simply made again later.
 * ponytail: whether an attach while stopped would stick is unknown; waiting for play covers both.
 */
void peq_chain(void) {
    if (!peq_chained && !g_equalizer_flag && mclGetPlayStatus() != 1) peq_attach();
}

/* Mono Audio (Q2POD MONO, read once): MONO_FILE holds it for hciplayer's filter and q2video, which
 * map it (peq_player.c, video.c), so a change is heard at once. The filter mixes after the EQ. */
int mono_on(void) {
    if (!xf.mono_read) xf.mono = config_value("Q2POD", "MONO", 2, 0), xf.mono_read = 1;
    return xf.mono;
}
int swap_on(void) {
    if (!xf.swap_read) xf.swap = config_value("Q2POD", "SWAPLR", 2, 0), xf.swap_read = 1;
    return xf.swap;
}
void mono_sync(void) {
    if (!xf.swap_flag && swap_on()) xf.swap_flag = shared_int(SWAP_FILE ".tmp", SWAP_FILE);
    if (xf.swap_flag) *xf.swap_flag = swap_on();
    if (xf.swap) peq_chain();
    if (!xf.mono_flag && (!mono_on() || !(xf.mono_flag = shared_int(MONO_FILE ".tmp", MONO_FILE))))
        return; /* no file reads as off */
    *xf.mono_flag = mono_on();
    if (xf.mono) peq_chain();
}

static void mono_text(void *label) { list_toggle(label, "Mono Audio", mono_on()); }

/* Playback's row (navigation.c ringnav_audioset makes it): the label, kept for mono_click. */
void mono_row(void *label) {
    xf.mono_label = label;
    mono_text(label);
}
int mono_click(void *ctx, void *event) {
    xf.mono = !mono_on();
    write_int_config(xf.mono, "Q2POD", "MONO");
    mono_sync();
    mono_text(xf.mono_label);
    return 0;
}

static void swap_text(void) { list_toggle(xf.swap_label, "Swap L/R", swap_on()); }
void swap_row(void *label) {
    xf.swap_label = label;
    swap_text();
}
int swap_click(void *ctx, void *event) {
    xf.swap = !swap_on();
    write_int_config(xf.swap, "Q2POD", "SWAPLR");
    mono_sync();
    swap_text();
    return 0;
}

/* The slider's value: the sensitivity; Resume's minutes, RS_MIN - 5 for Off; Night Shift's steps
 * below NS_COOL, on or off; or 0 for Off, else the length. */
static int value(void) {
    if (xf.ctx == 3) return (NS_COOL - night_kelvin()) / NS_STEP;
    if (xf.ctx == 2) return resume_min() ? resume_min() : RS_MIN - 5;
    return xf.ctx ? wheel_pct() : xf.on ? xf.seconds : 0;
}
static const int steps[] = { 1, 10, 5, 1 }; /* a tick or +/-, by xf.ctx */

/* Whether the page has its switch: Crossfade's and Night Shift's. */
static int switched(void) { return xf.ctx == 0 || xf.ctx == 3; }

/* The page and the row from xf, and the choice saved: during a drag, at its release (slid). */
static void refresh(int save) {
    if (save && xf.sliding) xf.unsaved = 1, save = 0;
    if (save) xf.unsaved = 0;
    if (save && xf.ctx == 1) write_int_config(wheel_pct(), "Q2POD", "WHEELSENSITIVITY");
    if (save && xf.ctx == 2) write_int_config(xf.rs_min, "Q2POD", "RESUMEMIN");
    if (save && xf.ctx == 3) night_save();
    if (save && !xf.ctx) {
        write_int_config(xf.on, "Q2POD", "XFADE");
        write_int_config(xf.seconds, "Q2POD", "XFADESEC");
    }
    void *row = widget_lookup(window_manager(), XF_ROW, 1);
    if (row) row_text(row);
    if ((row = widget_lookup(window_manager(), WHEEL_ROW, 1))) wheel_text(row);
    if ((row = widget_lookup(window_manager(), RS_ROW, 1))) rs_text(row);
    if ((row = widget_lookup(window_manager(), NS_ROW, 1))) night_text(row);
    if (!xf.page) return;
    char t[16];
    if (xf.ctx == 1)
        tk_snprintf(t, sizeof t, "%d%%", wheel_pct());
    else if (xf.ctx == 2)
        tk_snprintf(t, sizeof t, xf.rs_min ? "%d min" : "Off", xf.rs_min);
    else if (xf.ctx == 3) {
        tk_snprintf(t, sizeof t,
                    night_on() == 2 ? "Auto"
                    : night_on()    ? "%dK"
                                    : "Off",
                    night_kelvin());
        image_base_set_image(xf.sw, night_on() ? "switch_on" : "switch_off");
    } else {
        tk_snprintf(t, sizeof t, xf.on ? "%d s" : "Off", xf.seconds);
        image_base_set_image(xf.sw, xf.on ? "switch_on" : "switch_off");
    }
    widget_set_text_utf8(xf.label, t);
    if (switched()) slider_tray_dirty(xf.page);
    if (widget_get_prop_int(xf.slider, "value", 0) != value()) {
        xf.setting = 1; /* its value-changed is no release */
        slider_set_value(xf.slider, value());
        xf.setting = 0;
    }
}

static void take(int n) {
    if (xf.ctx == 3) { /* a move turns it on, Auto kept */
        int top = (NS_COOL - NS_WARM) / NS_STEP;
        n = n < 0 ? 0 : n > top ? top : n;
        if (n == value() && night_on()) return;
        /* shown at once, saved as the rest (refresh) */
        night_set(night_on() ? night_on() : 1, NS_COOL - n * NS_STEP);
        refresh(1);
        return;
    }
    if (xf.ctx == 2) {
        n = n < RS_MIN ? 0 : n > RS_MAX ? RS_MAX : (n + 2) / 5 * 5; /* the nearest five */
        if (n == resume_min()) return;
        xf.rs_min = n;
        refresh(1);
        return;
    }
    if (xf.ctx) {
        int was = wheel_pct();
        wheel_set_pct(n); /* rounds and clamps */
        refresh(wheel_pct() != was); /* and snaps the slider to the ten */
        return;
    }
    n = n < 0 ? 0 : n > XF_MAX ? XF_MAX : n;
    if (n == value()) return;
    xf.on = n > 0;
    if (n) xf.seconds = n < XF_MIN ? XF_MIN : n;
    refresh(1);
}

/* n, held: a finger dragging the slider, whose values save at its release. Anything else (the
 * release, the wheel, +/-) saves, a held value left unsaved too: stock's slider sends no change
 * for a drag back to its start or an aborted press, so a drag can end unseen. */
static void set_value(int n, int held) {
    xf.sliding = held;
    take(n);
    if (!held && xf.unsaved) refresh(1);
}

static int toggle(void *ctx, void *event) {
    if (!switched()) return 0;
    xf.sliding = 0;
    if (xf.ctx == 3) {
        night_set((night_on() + 1) % 3, night_kelvin()); /* Off, On, Auto */
        refresh(1);
        return 0;
    }
    xf.on = !xf.on;
    refresh(1);
    return 0;
}
static int step(void *ctx, void *event) {
    set_value(value() + (int)(long)ctx * steps[xf.ctx], 0);
    return 0;
}
/* A drag's values (ctx 1) show and play at once; the release (ctx 0) saves the last. */
static int slid(void *ctx, void *event) {
    if (!xf.setting) set_value(widget_get_prop_int(xf.slider, "value", value()), ctx != 0);
    return 0;
}
static int gone(void *ctx, void *event) {
    if (xf.page != ctx) return 0;
    xf.sliding = 0;
    if (xf.unsaved) refresh(1); /* a release never seen */
    stop_timer(&xf.press); /* a single press still waiting for its double: it was this page's */
    xf.page = 0, xf.ctx = 0;
    return 0;
}

int xfade_page(void *top) { return xf.page && top == xf.page; }

/* The row's click: stock's Boot volume page, its handlers swapped for these; ctx 1 for Wheel
 * sensitivity's row, 2 for Resume Songs', 3 for Night Shift's. */
int xfade_open(void *ctx, void *event) {
    int mode = (int)(long)ctx, wheel = mode == 1;
    settings();
    if (xf.page) return 0;
    void *wm = window_manager(), *below = window_manager_get_top_window(wm);
    navigator_to("playset/bootvol_page");
    void *page = window_manager_get_top_window(wm);
    if (page == below) return 0;
    void *nav = widget_lookup(page, "view_navbar", 1), *title = nav ? widget_get_child(nav, 0) : 0;
    xf.sw = widget_lookup(page, "img_bootswitch", 1);
    xf.view = widget_lookup(page, "view_bootvol", 1);
    xf.slider = widget_lookup(page, "slider_bootvol", 1);
    xf.dec = widget_lookup(page, "img_dec", 1);
    xf.add = widget_lookup(page, "img_add", 1);
    xf.label = widget_lookup(page, "label_vol", 1);
    if (tk_strcmp(widget_get_prop_str(page, "name", ""), "bootvol_page") || !title || !xf.sw ||
        !xf.view || !xf.slider || !xf.dec || !xf.add || !xf.label) {
        window_close(page);
        return 0;
    }
    widget_off_by_func(xf.sw, EVT_CLICK, (void *)BOOTVOL_SWITCH, page);
    widget_off_by_func(xf.dec, EVT_CLICK, (void *)BOOTVOL_DEC, page);
    widget_off_by_func(xf.add, EVT_CLICK, (void *)BOOTVOL_ADD, page);
    widget_off_by_func(xf.slider, EVT_VALUE_CHANGING, (void *)BOOTVOL_CHANGING, page);
    widget_off_by_func(xf.slider, EVT_VALUE_CHANGED, (void *)BOOTVOL_CHANGED, page);
    widget_on(xf.sw, EVT_CLICK, toggle, 0);
    widget_on(xf.dec, EVT_CLICK, step, (void *)-1L);
    widget_on(xf.add, EVT_CLICK, step, (void *)1L);
    widget_on(xf.slider, EVT_VALUE_CHANGING, slid, (void *)1L);
    widget_on(xf.slider, EVT_VALUE_CHANGED, slid, 0);
    widget_on(page, EVT_DESTROY, gone, page);
    widget_set_text_utf8(title, mode == 3   ? "Night Shift"
                                : mode == 2 ? "Resume Songs"
                                : wheel     ? "Wheel Sensitivity"
                                            : "Crossfade");
    /* Stock shows and enables it only while Boot volume's switch is on: disabled, touch misses the
     * slider and +/- (#140). */
    widget_set_visible(xf.view, 1, 0);
    widget_set_enable(xf.view, 1);
    slider_number(xf.label);
    xf.ctx = mode;
    if (!switched()) widget_set_visible(xf.sw, 0, 0);
    slider_set_min(xf.slider, mode == 2 ? RS_MIN - 5 : wheel ? WHEEL_PCT_MIN : 0);
    slider_set_max(xf.slider, mode == 3   ? (NS_COOL - NS_WARM) / NS_STEP
                              : mode == 2 ? RS_MAX
                              : wheel     ? 200
                                          : XF_MAX);
    slider_set_step(xf.slider, steps[mode]);
    xf.page = page;
    if (switched()) slider_centre_hint(page, mode == 3);
    refresh(0);
    return 0;
}

/* A single centre press, DOUBLE_CLICK_MS on: the switch. */
static int single(const void *info) {
    xf.press = 0;
    toggle(0, 0);
    return 0;
}

/* ringnav(), the page on top: Centre toggles, a double press turns the screen off as on every
 * page, and the wheel sets the length, down to Off. Wheel sensitivity: the wheel a ten a tick
 * (not through +/-, which stock may have disabled), Centre to edit_key, which goes back. */
int xfade_key(void *top, unsigned key) {
    static const unsigned release[EVENT_KEY / 4 + 1] = { [EVENT_KEY / 4] = KEY_CENTER };
    if (!xf.page || top != xf.page) return 0;
    if (key == KEY_CENTER) {
        if (!switched()) return 0;
        int press = center_press(&xf.press, &xf.press_at, single, 0);
        if (press == 2 && g_backlight_status) on_wm_keyup_fun((void *)0, (void *)release);
        return press;
    }
    if (key != KEY_NEXT && key != KEY_PREV) return 0;
    set_value(value() + (key == KEY_NEXT ? 1 : -1) * steps[xf.ctx], 0);
    return 1;
}

/* playback_poll, each UI pass: the next change's fade into XFADE_FILE, made whole under another
 * name first as the visualizer's tap is. A fade only when Crossfade and stock's Gapless are on and
 * the next track is another album's (album_cmp), so albums stay gapless; stock Shuffle's next
 * always counts as another album's, and is -2 when not drawn ahead (playback.c shuffle_peek). Its
 * change is a cold start (stock preloads nothing in Shuffle), which the filter's tail waits out
 * (XF_COLD_NS). Each stop moves the count beside the fade, so no tail outlives a stop: status 1 and
 * no file error. An error's status is no stop: stock's dialog timer takes the file away and shows
 * its modal toast, passes running under it, before it plays the next (on_wm_timer_dialog,
 * 0x4f4d18). A stop during a toast isn't counted. */
static int fades_into(void *r) {
    const char *p = P(r, REC_PATH), *e = p ? strrchr(p, '.') : 0;
    unsigned st[36];
    /* DSD skips the filter; a missing or empty file never plays: nothing would mix the tail */
    if (e && (!strcasecmp(e, ".dsf") || !strcasecmp(e, ".dff") || !strcasecmp(e, ".iso"))) return 0;
    /* over 2 GB, o32's stat fails: that's a file */
    return !p || p[0] != '/' || (!access(p, 0) && (__xstat(3, p, st) || st[12]));
}
void xfade_poll(int next, int shuffled) {
    void *q = P(mcl_pdeqplaylist, 0);
    unsigned pos = (unsigned)I(MCL_POS, 0), n = q ? deque_size(q) : 0;
    if (!xf.flag && (next == -1 || pos >= n)) return; /* nothing playing on: no config read yet */
    settings();
    if (xf.flag && *xf.flag & XF_MS) peq_chain(); /* the fade runs in the filter, once something plays */
    int in = next >= 0 && pos < n && (unsigned)next < n; /* records, not positions: a new queue */
    if (mclGetPlayStatus() != 1)
        xf.counted = 0;
    else if (xf.flag && !xf.counted && access("/tmp/notfile", 0) && access("/tmp/notsupport", 0) &&
             !navigator_window_is_exist("msginfo_dialog"))
        xf.counted = 1, ++xf.stops;
    /* the rows' paths too: Play Next moves rows' bytes, so a row's address can hold another song */
    int key[8] = { xf.on ? xf.seconds : 0, *(volatile unsigned char *)MCL_GAPLESS,
                   in ? (int)deque_at(q, pos) : 0, in ? (int)deque_at(q, (unsigned)next) : 0,
                   shuffled && next != -1 && pos < n, xf.stops,
                   in ? (int)P(deque_at(q, pos), REC_PATH) : 0,
                   in ? (int)P(deque_at(q, (unsigned)next), REC_PATH) : 0 };
    if (xf.done && !memcmp(key, xf.key, sizeof key)) return;
    memcpy(xf.key, key, sizeof key);
    int ms = key[0] && key[1] &&
                     (key[4] || (key[2] != key[3] && album_cmp((void *)key[2], (void *)key[3]))) &&
                     (!key[3] || fades_into((void *)key[3]))
                 ? key[0] * 1000
                 : 0;
    xf.done = 0; /* until the player is told: a file that cannot be made yet is tried next pass */
    if (!xf.flag && ms && !(xf.flag = shared_int(XFADE_FILE ".tmp", XFADE_FILE))) return;
    if (xf.flag) *xf.flag = ms | (xf.stops & 0x7fff) << XF_STOPS;
    xf.done = 1;
}
