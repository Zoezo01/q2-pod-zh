/* Crossfade (docs/internals.md#crossfade): Audio settings' row, its page and the fade hciplayer's
 * filter applies (peq_player.c). The page is stock's Boot volume page re-dressed: its slider runs
 * Off (0) and XF_MIN..XF_MAX seconds, so it needs no switch; the switch and Centre still turn it on
 * and off. Display's Wheel sensitivity row opens the same page (xf.ctx 1): 50-200% in tens, no
 * switch, the wheel a ten a tick and Centre back, as on Brightness (navigation.c edit_key). Audio
 * settings' Resume Songs row opens it too (xf.ctx 2): Off and RS_MIN..RS_MAX minutes in fives, the
 * same way, the length a music track needs to keep its place (navigation.c resume_poll). Mono
 * Audio's and MQA's rows live here too (mono_sync, mqa_sync). */
#include "offsets.inc"
#include "peq.h"
#include "playback.h"
#include "stock.h"

#define XF_MIN 1
#define XF_MAX 10
#define XF_DEFAULT 5
#define XF_ROW "label_xfade"     /* the Audio settings row's label, found again by name */
#define WHEEL_ROW "label_wheel"  /* and Display's Wheel sensitivity row's */
#define RS_ROW "label_resumemin" /* and Audio settings' Resume Songs row's */
#define RS_MIN 20
#define RS_MAX 120
#define RS_DEFAULT 30

static struct {
    int read, on, seconds, done, key[4];
    int ctx; /* the page's: 0 Crossfade, 1 Wheel sensitivity, 2 Resume Songs */
    int rs_read, rs_min;
    int mono_read, mono, swap_read, swap, mqa_read, mqa;
    volatile int *flag, *mono_flag, *swap_flag;
    void *swap_label, *mqa_label;
    void *mono_label; /* the Mono Audio row's, while Audio settings is open */
    void *page, *sw, *view, *slider, *dec, *add, *label;
    unsigned press, press_at;
} xf __attribute__((section(".scratch")));

/* Q2POD XFADE (0, 1) and XFADESEC (XF_MIN..XF_MAX) in config.ini, read once. */
static void settings(void) {
    if (xf.read) return;
    char v[16] = "";
    xf.on = config_value("Q2POD", "XFADE", 2, 0);
    toolsReadConfig("/mnt/data/config.ini", "Q2POD", "XFADESEC", v, "0");
    int n = atoi(v);
    xf.seconds = n >= XF_MIN && n <= XF_MAX ? n : XF_DEFAULT;
    xf.read = 1;
}

static void row_text(void *label) {
    char t[32] = "交叉淡化：关闭";
    if (xf.on) tk_snprintf(t, sizeof t, "交叉淡化：%d s", xf.seconds);
    widget_set_text_utf8(label, t);
}

/* Audio settings' row (navigation.c ringnav_audioset makes it): the label, named for refresh(). */
void xfade_row(void *label) {
    settings();
    widget_set_name(label, XF_ROW);
    row_text(label);
}

static void wheel_text(void *label) {
    char t[32];
    tk_snprintf(t, sizeof t, "滚轮灵敏度：%d%%", wheel_pct());
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
        char v[16] = "";
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
    char t[32] = "断点续播：关闭";
    if (resume_min()) tk_snprintf(t, sizeof t, "断点续播：%d 分钟", resume_min());
    widget_set_text_utf8(label, t);
}

/* Audio settings' row (navigation.c ringnav_audioset makes it): the label, named for refresh(). */
void resume_row(void *label) {
    widget_set_name(label, RS_ROW);
    rs_text(label);
}

/* An int shared with hciplayer's filter (and q2video): made whole under tmp and renamed, as the
 * visualizer's tap is, then mapped; 0 if it cannot be made yet, so the caller tries next pass. */
static volatile int *shared_int(const char *tmp, const char *path) {
    void *p = (void *)-1;
    int fd = open(tmp, 0x302, 0644); /* O_RDWR | O_CREAT | O_TRUNC (MIPS) */
    if (fd < 0) return 0;
    if (!ftruncate(fd, sizeof(int))) {
        p = mmap(0, sizeof(int), 3, 1, fd, 0); /* PROT_READ | PROT_WRITE, MAP_SHARED */
        if (p != (void *)-1 && rename(tmp, path)) {
            munmap(p, sizeof(int));
            p = (void *)-1;
        }
    }
    close(fd);
    return p != (void *)-1 ? p : 0;
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

static void mono_text(void *label) { list_toggle(label, "单声道音频", mono_on()); }

/* Audio settings' row (navigation.c ringnav_audioset makes it): the label, kept for mono_click. */
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

static void swap_text(void) { list_toggle(xf.swap_label, "左右声道互换", swap_on()); }
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

/* MQA (Q2POD MQA, read once, default off). hciplayer's start-of-track probe decodes about 2 s of a
 * FLAC or PCM track before it plays (docs/internals.md#mqa-detection), long enough to break a
 * gapless change. Off sends stock's {mcl-mqadecodemode\2}, which skips the probe, so MQA plays
 * undecoded; On sends 0, hciplayer's default. hciplayer keeps it until it restarts, and
 * ringnav_mode sends it again from player_initconfig, which runs on every connect. A change applies
 * at the next audio init. */
static int mqa_on(void) {
    if (!xf.mqa_read) xf.mqa = config_value("Q2POD", "MQA", 2, 0), xf.mqa_read = 1;
    return xf.mqa;
}
void mqa_sync(void) {
    static const char on[] = "{mcl-mqadecodemode\\0}", off[] = "{mcl-mqadecodemode\\2}";
    int fd = *(volatile int *)MCL_FD;
    if (fd != -1) send(fd, mqa_on() ? on : off, sizeof on - 1, 0);
}
static void mqa_text(void) { list_toggle(xf.mqa_label, "MQA", mqa_on()); }
void mqa_row(void *label) {
    xf.mqa_label = label;
    mqa_text();
}
int mqa_click(void *ctx, void *event) {
    xf.mqa = !mqa_on();
    write_int_config(xf.mqa, "Q2POD", "MQA");
    mqa_sync();
    mqa_text();
    return 0;
}

/* The slider's value: the sensitivity; Resume's minutes, RS_MIN - 5 for Off; or 0 for Off, else
 * the length. */
static int value(void) {
    if (xf.ctx == 2) return resume_min() ? resume_min() : RS_MIN - 5;
    return xf.ctx ? wheel_pct() : xf.on ? xf.seconds : 0;
}
static const int steps[] = { 1, 10, 5 }; /* a tick or +/-, by xf.ctx */

/* The page and the row from xf, and the choice saved. */
static void refresh(int save) {
    if (save && xf.ctx == 2) write_int_config(xf.rs_min, "Q2POD", "RESUMEMIN");
    if (save && !xf.ctx) {
        write_int_config(xf.on, "Q2POD", "XFADE");
        write_int_config(xf.seconds, "Q2POD", "XFADESEC");
    }
    void *row = widget_lookup(window_manager(), XF_ROW, 1);
    if (row) row_text(row);
    if ((row = widget_lookup(window_manager(), WHEEL_ROW, 1))) wheel_text(row);
    if ((row = widget_lookup(window_manager(), RS_ROW, 1))) rs_text(row);
    if (!xf.page) return;
    char t[16];
    if (xf.ctx == 1)
        tk_snprintf(t, sizeof t, "%d%%", wheel_pct());
    else if (xf.ctx == 2)
        tk_snprintf(t, sizeof t, xf.rs_min ? "%d 分钟" : "关闭", xf.rs_min);
    else {
        tk_snprintf(t, sizeof t, xf.on ? "%d s" : "关闭", xf.seconds);
        image_base_set_image(xf.sw, xf.on ? "switch_on" : "switch_off");
    }
    widget_set_text_utf8(xf.label, t);
    if (widget_get_prop_int(xf.slider, "value", 0) != value()) slider_set_value(xf.slider, value());
}

static void set_value(int n) {
    if (xf.ctx == 2) {
        n = n < RS_MIN ? 0 : n > RS_MAX ? RS_MAX : (n + 2) / 5 * 5; /* the nearest five */
        if (n == resume_min()) return;
        xf.rs_min = n;
        refresh(1);
        return;
    }
    if (xf.ctx) {
        wheel_set_pct(n); /* rounds, clamps and saves */
        refresh(0);
        return;
    }
    n = n < 0 ? 0 : n > XF_MAX ? XF_MAX : n;
    if (n == value()) return;
    xf.on = n > 0;
    if (n) xf.seconds = n < XF_MIN ? XF_MIN : n;
    refresh(1);
}

static int toggle(void *ctx, void *event) {
    if (xf.ctx) return 0;
    xf.on = !xf.on;
    refresh(1);
    return 0;
}
static int step(void *ctx, void *event) {
    set_value(value() + (int)(long)ctx * steps[xf.ctx]);
    return 0;
}
static int slid(void *ctx, void *event) {
    set_value(widget_get_prop_int(xf.slider, "value", value()));
    return 0;
}
static int gone(void *ctx, void *event) {
    if (xf.page == ctx) xf.page = 0, xf.ctx = 0;
    return 0;
}

/* The row's click: stock's Boot volume page, its handlers swapped for these; ctx 1 for Wheel
 * sensitivity's row, 2 for Resume Songs'. */
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
    widget_on(xf.slider, EVT_VALUE_CHANGING, slid, 0);
    widget_on(xf.slider, EVT_VALUE_CHANGED, slid, 0);
    widget_on(page, EVT_DESTROY, gone, page);
    widget_set_text_utf8(title, mode == 2 ? "断点续播"
                                : wheel   ? "滚轮灵敏度"
                                          : "交叉淡化");
    widget_set_visible(xf.view, 1, 0); /* stock shows it only while Boot volume's switch is on */
    if (mode) widget_set_visible(xf.sw, 0, 0);
    slider_set_min(xf.slider, mode == 2 ? RS_MIN - 5 : wheel ? 50 : 0);
    slider_set_max(xf.slider, mode == 2 ? RS_MAX : wheel ? 200 : XF_MAX);
    slider_set_step(xf.slider, steps[mode]);
    xf.ctx = mode;
    xf.page = page;
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
        if (xf.ctx) return 0;
        int press = center_press(&xf.press, &xf.press_at, single, 0);
        if (press == 2 && g_backlight_status) on_wm_keyup_fun((void *)0, (void *)release);
        return press;
    }
    if (key != KEY_NEXT && key != KEY_PREV) return 0;
    set_value(value() + (key == KEY_NEXT ? 1 : -1) * steps[xf.ctx]);
    return 1;
}

/* playback_poll, each UI pass: the next change's fade into XFADE_FILE, made whole under another
 * name first as the visualizer's tap is. A fade only when Crossfade and stock's Gapless are on and
 * the next track is another album's (album_cmp), so albums stay gapless. */
void xfade_poll(int next) {
    void *q = P(mcl_pdeqplaylist, 0);
    unsigned pos = (unsigned)I(MCL_POS, 0), n = q ? deque_size(q) : 0;
    if (!xf.flag && (next < 0 || pos >= n)) return; /* nothing playing on: no config read yet */
    settings();
    if (xf.flag && *xf.flag) peq_chain(); /* the fade runs in the filter, once something plays */
    int in = next >= 0 && pos < n && (unsigned)next < n; /* records, not positions: a new queue */
    int key[4] = { xf.on ? xf.seconds : 0, *(volatile unsigned char *)MCL_GAPLESS,
                   in ? (int)deque_at(q, pos) : 0, in ? (int)deque_at(q, (unsigned)next) : 0 };
    if (xf.done && !memcmp(key, xf.key, sizeof key)) return;
    memcpy(xf.key, key, sizeof key);
    int ms = key[0] && key[1] && key[2] != key[3] && album_cmp((void *)key[2], (void *)key[3])
                 ? key[0] * 1000
                 : 0;
    xf.done = 0; /* until the player is told: a file that cannot be made yet is tried next pass */
    if (!xf.flag && ms && !(xf.flag = shared_int(XFADE_FILE ".tmp", XFADE_FILE))) return;
    if (xf.flag) *xf.flag = ms;
    xf.done = 1;
}
