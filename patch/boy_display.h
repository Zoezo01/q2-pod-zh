/* SPDX-License-Identifier: GPL-3.0-only */
/* Pocket Boy requests a temporary brightness change while its own menus are idle. */
#define BOY_DISPLAY_REQUEST "/tmp/q2-boy.display.req"
#define BOY_DISPLAY_DIMMED "/tmp/q2-boy.display.dimmed"

static struct {
    unsigned polled;
    int active, foreground, dimmed;
} bd __attribute__((section(".scratch")));

static void boy_display_restore(void) {
    if (!bd.dimmed) return;
    bd.dimmed = 0;
    if (g_backlight_status) toolSetBackLight(g_lightness + 1);
    unlink(BOY_DISPLAY_DIMMED);
}

static void boy_display_start(void) {
    unlink(BOY_DISPLAY_REQUEST);
    unlink(BOY_DISPLAY_DIMMED);
    bd.polled = 0;
    bd.active = bd.foreground = 1;
}

static void boy_display_poll(int pid, int foreground) {
    if (!pid || !foreground || !g_backlight_status) {
        if (bd.foreground) {
            boy_display_restore();
            unlink(BOY_DISPLAY_REQUEST);
        }
        bd.foreground = 0;
        bd.active = pid != 0;
        bd.polled = 0;
        return;
    }
    bd.foreground = 1;
    unsigned now = (unsigned)time_now_ms();
    if (bd.polled && now - bd.polled < (bd.dimmed ? 80u : 250u)) return;
    bd.polled = now;
    char requested;
    if (BLOB_IO(BOY_DISPLAY_REQUEST, requested, 0) && requested == '1') {
        /* Stock brightness is zero-based. 1 is its minimum lit setting. */
        if (!bd.dimmed && g_lightness && BLOB_IO(BOY_DISPLAY_DIMMED, requested, 1)) {
            toolSetBackLight(1);
            bd.dimmed = 1;
        }
    } else {
        boy_display_restore();
    }
}
