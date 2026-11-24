/* A held Previous or Next with the screen off seeks within the playing song (docs/internals.md,
 * Scrub (iPod)): stock's long press arms it, each repeat of the held key moves the target
 * SIDE_SEEK_S, and the release seeks there once instead of skipping. Lit, Now Playing's own key
 * seek has the hold as stock's; stock's key-up ignores it dark (0x53da5c tests
 * g_backlight_status) and skips, leaving its long byte set, so a dark hold is taken everywhere. */
#include "navigation.h"

#define SIDE_SEEK_S 5        /* stock's key seek step, one per repeat (0x53e000) */
#define NP_KEY_LONG 0xa4e880 /* stock: Now Playing's side-button long press (0x53b4d0) */

static struct {
    unsigned key, track; /* the held key, 0 none; np_track() when it armed */
    int to, total;       /* the target and the song's length, in seconds */
} ss __attribute__((section(".scratch")));

/* phase: 1 a press or its repeat, 2 the release, 3 stock's long press. Returns 1 to stop the
 * event. Only local music playing arms it, not under Buttons lock's Everything or Buttons only.
 * ponytail: no sound while held; the seek lands at the release, as stock's lit key seek. A lost
 * release (Standby mid-hold) leaves it armed: that key's next press seeks to the old target, once.
 */
int side_seek(unsigned key, int phase) {
    if (key != KEY_BACK_BTN && key != KEY_FWD_BTN) return 0;
    if (phase == 3 && !ss.key && !g_backlight_status && mclGetPlayStatus() == 2 &&
        media_owner() == 1 && !media_focused() && !addon_game_audio() &&
        !(g_keylock_flag && (g_keylock_mode == 2 || g_keylock_mode == 3))) {
        player_playtime_and_length(&ss.to, &ss.total);
        if (ss.to >= 0) ss.key = key, ss.track = np_track();
    }
    if (ss.key != key) return 0;
    if (phase == 1) {
        ss.to += key == KEY_FWD_BTN ? SIDE_SEEK_S : -SIDE_SEEK_S;
        if (ss.to < 0) ss.to = 0;
        if (ss.total > 0 && ss.to > ss.total) ss.to = ss.total;
    } else if (phase == 2) {
        ss.key = 0;
        *(volatile unsigned char *)NP_KEY_LONG = 0;
        if (np_track() == ss.track && mclGetPlayStatus() == 2) player_seek_time(ss.to);
    }
    return 1;
}
