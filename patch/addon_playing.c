/* The song playing, for card apps: the word "playing" in /tmp/q2-addon.caps (addon.c ADDON_CAPS;
 * docs/librespot.md). While the player plays or pauses a song from the card, PLAYING_FILE holds its
 * path (/mnt/mmc/…, no line break); otherwise there is no file: stopped, nothing loaded, or a song
 * not on the card (a URL queue's address can carry an app's token, so it is never written). An app
 * runs without root and can't read the player's /proc/<pid>/fd, so this is how it knows. Written
 * whole (blob_io, tmpfs) or removed only on a change, looked at every PLAYING_POLL_MS from
 * addon_poll; the first look after a start writes or removes it, so a crashed run's file never
 * stands. */
#include "offsets.inc"
#include "peq.h"

#define PLAYING_FILE "/tmp/q2-playing"
#define PLAYING_POLL_MS 500

void *queue_now(unsigned *pos, unsigned *n); /* coverflow.c */

static struct {
    unsigned said; /* fnv of the path the file says ("" none) + 1; 0 before the first look */
    unsigned polled;
} pl __attribute__((section(".scratch")));

void addon_playing_poll(void) {
    unsigned ms = time_now_ms(), pos, n;
    if (pl.said && ms - pl.polled < PLAYING_POLL_MS) return;
    pl.polled = ms;
    int status = mclGetPlayStatus(); /* 2 playing, 3 paused */
    void *r = status == 2 || status == 3 ? queue_now(&pos, &n) : 0;
    const char *p = r ? P(r, REC_PATH) : 0;
    if (!p || !tk_str_start_with(p, "/mnt/mmc/")) p = "";
    unsigned h = fnv(FNV_SEED, (const unsigned char *)p) + 1;
    if (h == pl.said) return;
    if (*p ? blob_io(PLAYING_FILE, PLAYING_FILE ".tmp", (void *)p, strlen(p), 1)
           : !unlink(PLAYING_FILE) || access(PLAYING_FILE, 0))
        pl.said = h;
}
