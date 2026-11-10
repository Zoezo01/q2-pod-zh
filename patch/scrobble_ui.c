/* Scrobbling's UI side: the automatic upload and Upload Scrobbles, which start and report
 * scrobble.c's upload thread. */
#include "navigation.h"

/* [SCROBBLE] AUTO=1 (docs/internals.md#scrobbling): on Wi-Fi the log goes up by itself, silently,
 * once its oldest unsent listen is SA_MAX_MIN old, SA_STEP_MIN sooner per listen since, at least
 * every SA_MIN_MIN: a listen on its own waits three hours, an afternoon's album goes within the
 * hour, and with no listens nothing goes. A failed upload tries again after SA_RETRY_MIN.
 * ponytail: only listens since boot and outside an upload count; any others stay in the log and
 * go with the next upload, so the loop never reads the card for them. */
#define SA_MAX_MIN 180
#define SA_MIN_MIN 60
#define SA_STEP_MIN 4
#define SA_RETRY_MIN 30
static struct {
    unsigned pending, first_at, check_at;
    int running, woke; /* woke: Smart Wi-Fi's radio woken for this upload */
} sa __attribute__((section(".scratch")));
unsigned scrobble_auto_ms(unsigned pending) {
    int min = SA_MAX_MIN - SA_STEP_MIN * (int)pending;
    return (unsigned)(min < SA_MIN_MIN ? SA_MIN_MIN : min) * 60000u;
}
void scrobble_listened(void) {
    if (!sa.pending++)
        sa.first_at = (unsigned)time_now_ms(), sa.woke = 0; /* a wake for the last batch's */
}
/* Due again SA_RETRY_MIN from now, sooner if more listens come. */
static void sa_retry(unsigned now) {
    sa.first_at = now - scrobble_auto_ms(sa.pending) + SA_RETRY_MIN * 60000u;
}
void scrobble_auto(void) {
    unsigned now = (unsigned)time_now_ms();
    if (sa.running) {
        int sent = 0, done = scrobble_poll(&sent);
        if (!done) return;
        sa.running = 0;
        if (done > 0)
            sa.pending = 0;
        else
            sa_retry(now);
        return;
    }
    if (!sa.pending || now - sa.first_at < scrobble_auto_ms(sa.pending) ||
        now - sa.check_at < 60000)
        return;
    sa.check_at = now; /* Wi-Fi at most once a minute; the card's settings only on Wi-Fi */
    /* not while a computer has the card. ponytail: one already uploading when the cable goes in
     * still reads and rewrites the log, a window at a time, until it ends; join it in
     * ringnav_usbmode if that ever bites. */
    if (navigator_window_is_exist("usbmode_page")) return;
    if (get_wifisignal() <= 0) {
        /* Smart Wi-Fi's radio off: woken for an upload that is on (the card read now), which the
         * next check sends; a wake that brought no link, or AUTO off, again in SA_RETRY_MIN */
        if (sa.woke || (smart_wifi_off() && !scrobble_flag("AUTO")))
            sa.woke = 0, sa_retry(now);
        else if (smart_wifi_off())
            sa.woke = smart_wifi_wait();
        return;
    }
    sa.woke = 0;
    int started = scrobble_flag("AUTO") ? scrobble_start() : -1;
    if (started > 0)
        sa.running = 1;
    else if (started < 0) /* off, or no account: the card again in SA_RETRY_MIN */
        sa_retry(now);
}

/* Upload Scrobbles: the log goes up on scrobble.c's thread, and a timer reports how it went. */
static int upload_wait(const void *unused) {
    int sent = 0, done = scrobble_poll(&sent);
    if (!done) return 8; /* RET_REPEAT */
    if (done > 0) sa.pending = 0;
    char s[48];
    tk_snprintf(s, sizeof s, done < 0 ? "Upload failed, %d sent" : "Scrobbles sent: %d", sent);
    toast(done > 0 && !sent ? "Nothing to upload" : s);
    return 0;
}

int upload_scrobbles(void *ctx, void *event) {
    if (!need_link(upload_scrobbles, ctx)) return 0; /* held for Smart Wi-Fi's link, or none */
    int started = scrobble_start();
    if (!started && sa.running) sa.running = 0, started = 1; /* the automatic one: report it */
    if (started > 0) timer_add(upload_wait, (void *)0, 500);
    toast(started > 0 ? "Uploading scrobbles" : !started ? "Already uploading" : "Upload failed");
    return 0;
}
