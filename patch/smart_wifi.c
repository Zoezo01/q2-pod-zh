/* Smart Wi-Fi: the radio off while nothing uses the network, back for what needs it. */
#include "navigation.h"

/* Smart Wi-Fi (docs/internals.md#smart-wi-fi): with Power's row on and the user's Wi-Fi switch on,
 * the radio goes off once nothing has needed the network for SMART_IDLE_MS, and comes back as soon
 * as something does. Both go through stock's own switch, wifi_switch, as Quick Settings and the
 * Wi-Fi page call it: off clears its flags, stops SyncLink's server and runs networkStop
 * (network_checkstatus then sends ips=1), on runs networkStart's background wifi_connect.sh. Its
 * last act, the WIFI= write, is dropped for these (smart_config), so config.ini, a reboot and the
 * user's own switch stay stock's. off: Smart Wi-Fi turned the radio off; quiet: its own toggle
 * runs; busy_at: the last use; woke_at: its last wake, whose link smart_wifi_wait's callers wait
 * for; up: the link at the last poll; armed: the row was on then; hold_*: the action held for the
 * link (smart_hold), its argument, the page it was pressed on and that page's name, and the timer;
 * held: that action runs now, so it is never held again. */
static struct {
    unsigned at, busy_at, woke_at, hold_timer;
    int off, quiet, armed, up, held;
    int (*hold_fn)(void *, void *);
    void *hold_ctx, *hold_win;
    char hold_name[32];
} sw __attribute__((section(".scratch")));

/* Stock's own conditions for its switch: the network module up for Wi-Fi (NET_TYPE 2; networkStart
 * and networkStop do nothing otherwise), its first scan taken (wifi_initstatus 0, which Quick
 * Settings and the Wi-Fi page require) and no connect half done (wifi_connectstatus 1, the Wi-Fi
 * page's); never in test mode, while powering off or in the first-boot guide, where
 * on_wm_timer_networkstatus also leaves the network alone. */
static int smart_ready(void) {
    return *(volatile int *)NET_TYPE == 2 && !wifi_initstatus && wifi_connectstatus != 1 &&
           !g_testmode_flag && g_poweroff_state != 2 && (signed char)g_guideflag <= 0;
}

/* wifi_switch(1) for Smart Wi-Fi: the radio off when g_wififlag is set, else back on. */
static void smart_switch(unsigned now) {
    int wake = !g_wififlag;
    sw.quiet = 1;
    wifi_switch(1);
    sw.quiet = 0;
    sw.off = !g_wififlag;
    if (wake) sw.woke_at = now | !now; /* never 0; |1 would eat a millisecond when now is even */
    sw.busy_at = now;
}

/* Streaming's page and the Wi-Fi page: Smart Wi-Fi's radio back at once. */
void smart_wake(void) {
    if (sw.off && smart_ready()) smart_switch(time_now_ms());
}

/* write_int_config's hook (ringnav_write_config): wifi_switch's last act writes SYSSET WIFI. Smart
 * Wi-Fi's own toggles skip it (1). Any other is the user's (Quick Settings, the Wi-Fi page, test
 * mode): Smart Wi-Fi forgets it had the radio off and counts SMART_IDLE_MS from now (0). */
int smart_config(const char *section, const char *key) {
    if (tk_strcmp(section, "SYSSET") || tk_strcmp(key, "WIFI")) return 0;
    if (sw.quiet) return 1;
    sw.off = 0;
    sw.woke_at = 0;
    sw.busy_at = time_now_ms();
    return 0;
}

/* systemset_wifi_page_init: the Wi-Fi page builds its switch from g_wififlag, so the radio comes
 * back first and the page shows the user's setting. */
int ringnav_wifi_page(void *win, void *ctx) {
    smart_wake();
    return stock_wifi_page_trampoline(win, ctx);
}

/* A page that uses the network, open anywhere in the window stack: Streaming and its services'
 * pages (Spotify's, Internet Radio's, a card app's and its QR code, Tidal's but Tidal Downloads),
 * System settings' Apps, AirPlay's, Network service, SyncLink, the Wi-Fi page, Wi-Fi transfer and
 * their dialogs, and the online lyrics and cover search. */
static int net_page_open(void) {
    static const char *const pages[] = {
        "stream_page",         "spotify_page",    "spotbrowse_page",  "radio_page",
        "radionp_page",        "addon_page",      "qrcode_page",      "apps_page",
        "airplay_page",        "netservice_page", "synclink_page",    "wifi_page",
        "wifitransport_page",  "editwifi_dialog", "kbwifiadd_dialog", "kbwifipass_dialog",
        "onlinematching_page", "tidal_"
    };
    void *wm = window_manager();
    for (unsigned i = 0, n = widget_count_children(wm); i < n; ++i) {
        const char *name = widget_get_prop_str(widget_get_child(wm, i), "name", "");
        if (!tk_strcmp(name, "tidal_downloads")) continue; /* the card's tracks */
        for (unsigned k = 0; k < sizeof pages / sizeof *pages; ++k)
            if (tk_str_start_with(name, pages[k])) return 1;
    }
    return 0;
}

/* Something needs the network: a USB cable in; AirPlay or DLNA on (Network service), SyncLink over
 * Wi-Fi or a SyncLink session, Wi-Fi transfer; a stock stream queue (Tidal's: the types
 * on_wm_timer_networkstatus stops once offline, whatever it is doing, as Play must fetch its URL at
 * once), Spotify's librespot running or waiting to start (it never retries a Connect login or mDNS
 * that failed), a station playing, a card app running, a scrobble upload, Network Update's download
 * or an Apps job, Download Album Lyrics, a Tidal download or an action held for the link
 * (smart_hold); a time sync pending on a link; or a page above. */
static int smart_needed(void) {
    unsigned type = (unsigned)mclGetPlayType();
    return g_usbdet_value || g_airplayflag || g_dlnaflag || g_synclinkflag == 2 ||
           g_syncLink_status || g_wifitransport_flag || airplayGetFlag() == 2 || type - 2 < 2 ||
           type - 0xf1 < 2 || spot_running() || radio_status() == 2 || addon_running() ||
           scrobble_busy() || netupdate_busy() || album_lyrics_busy() || tidal_busy() ||
           sw.hold_timer || (g_syncnettime_flag && networkGetConnectStatus() > 0) ||
           net_page_open();
}

/* power_poll, every SMART_POLL_MS. The row off, or the user's Wi-Fi off: stock's, and a radio Smart
 * Wi-Fi had off comes back. Otherwise a use or a new link restarts the count, a use wakes the
 * radio, and SMART_IDLE_MS without one, associated or not, turns it off. */
void smart_poll(void) {
    unsigned now = time_now_ms();
    if (sw.at && now - sw.at < SMART_POLL_MS) return;
    sw.at = now | !now;
    /* ponytail: while stock refuses its switch (smart_ready), a waiter that asks after this expiry
     * starts another SMART_CONNECT_MS: one more 15 s at most; keep each waiter's own start if it
     * bites. */
    if (sw.woke_at && now - sw.woke_at >= SMART_CONNECT_MS) sw.woke_at = 0;
    if (!st.smart_wifi || (!g_wififlag && !sw.off)) {
        sw.armed = 0;
        smart_wake();
        return;
    }
    int up = networkGetConnectStatus() > 0;
    if (!sw.armed || (up && !sw.up))
        sw.busy_at = now; /* turned on, or a new link: time sync first */
    sw.armed = 1;
    sw.up = up;
    if (smart_needed()) {
        sw.busy_at = now;
        smart_wake();
    } else if (!sw.off && now - sw.busy_at >= SMART_IDLE_MS && smart_ready())
        smart_switch(now);
}

/* Smart Wi-Fi has the radio off (the user's switch is on). */
int smart_wifi_off(void) { return sw.off; }

/* Something that needs the link starting (Spotify's launch, a radio list or station, an automatic
 * scrobble upload): Smart Wi-Fi's radio woken now if it is off, and 1 while that wake's link is not
 * up yet, SMART_CONNECT_MS at most, so the caller waits for it; then, or with the radio as the user
 * left it, 0, and the caller goes on as stock's would. Up is both the status bar's bars, which the
 * payload's own actions check (get_wifisignal), and the monitor thread's connect status, which
 * stock's check (networkGetConnectStatus): each comes from its own poll, about a second apart. */
int smart_wifi_wait(void) {
    if (!sw.off && !sw.woke_at) return 0;
    unsigned now = time_now_ms();
    sw.busy_at = now;
    if (sw.off && !sw.woke_at) sw.woke_at = now | !now; /* the wait counts from the ask */
    if (sw.off && smart_ready()) smart_switch(now);
    if (now - sw.woke_at < SMART_CONNECT_MS &&
        (sw.off || get_wifisignal() <= 0 || networkGetConnectStatus() <= 0))
        return 1;
    sw.woke_at = 0;
    return 0;
}

/* The page in use: the top window, past stock's self-closing overlays: toasts (msginfo_dialog, ours
 * or stock's) and the volume (volume_dialog, which a wheel turn opens over a toast). */
static void *page_in_use(void) {
    void *wm = window_manager();
    for (int i = (int)widget_count_children(wm) - 1; i >= 0; --i) {
        void *w = widget_get_child(wm, i);
        const char *name = widget_get_prop_str(w, "name", "");
        if (tk_strcmp(name, "msginfo_dialog") && tk_strcmp(name, "volume_dialog")) return w;
    }
    return (void *)0;
}

static int hold_link(const void *unused) {
    /* A page closing mid-slide is still the top window (iPod): the next tick looks again. */
    if (window_manager_is_animating(window_manager())) return 8; /* RET_REPEAT */
    void *win = sw.hold_win;
    int held = page_in_use() == win; /* compared first: a closed page is never read */
    if (held && smart_wifi_wait()) return 8;
    int (*fn)(void *, void *) = sw.hold_fn;
    sw.hold_fn = 0;
    sw.hold_timer = 0;
    if (held && !tk_strcmp(widget_get_prop_str(win, "name", ""), sw.hold_name)) {
        sw.held = 1; /* a radio that could not come back fails it now, as without Smart Wi-Fi */
        fn(sw.hold_ctx, (void *)0);
        sw.held = 0;
    }
    return 0;
}

/* An action that needs the link, pressed while Smart Wi-Fi brings the radio back (smart_wifi_wait),
 * on page (any page when 0): 1, "Connecting to Wi-Fi…", and fn(ctx, 0) runs from a 500 ms timer
 * once the link is up, or SMART_CONNECT_MS on (to fail as it would have), if the page it was
 * pressed on is still the one in use past toasts and the volume overlay; otherwise it is dropped.
 * 0: it runs now, as without Smart Wi-Fi: the radio as the user left it, the link up, another page,
 * or no timer. One waits at a time: a press while one waits takes its place, link or not, so it
 * never runs beside the held one. fn reads no event. */
int smart_hold(int (*fn)(void *, void *), void *ctx, const char *page) {
    void *win = page_in_use();
    const char *name = win ? widget_get_prop_str(win, "name", "") : "";
    if (sw.held || !win || (page && tk_strcmp(name, page)) || strlen(name) >= sizeof sw.hold_name)
        return 0;
    if (!sw.hold_timer &&
        (!smart_wifi_wait() || !(sw.hold_timer = timer_add(hold_link, (void *)0, 500))))
        return 0;
    sw.hold_fn = fn;
    sw.hold_ctx = ctx;
    sw.hold_win = win;
    tk_snprintf(sw.hold_name, sizeof sw.hold_name, "%s", name);
    toast("Connecting to Wi-Fi\xe2\x80\xa6");
    return 1;
}

/* A press of the payload's that needs the link, fn(ctx, event) itself: 1, it goes on; 0, it waits
 * for Smart Wi-Fi's link (smart_hold) or, with none coming, "Connect to Wi-Fi first". Held first,
 * so a second press after the link is up but before the held one runs joins it rather than running
 * beside it (and cancelling it). */
int need_link(int (*fn)(void *, void *), void *ctx) {
    if (smart_hold(fn, ctx, 0)) return 0;
    if (get_wifisignal() > 0) return 1;
    toast("Connect to Wi-Fi first");
    return 0;
}

/* Stock actions that check the link once (networkGetConnectStatus) and say msg_notnetwork without
 * it, held the same way: Streaming's Tidal row, Network service's Wi-Fi transfer and Now Playing
 * More's online lyrics and cover match. None reads its event, and the rows are found again by name
 * (stock names each row's list_item and button by its index and reads only that name), so nothing
 * pressed is kept but the page. */
static int row_of(void *button) {
    const char *name = widget_get_prop_str(button, "name", "");
    return name[0] >= '0' && name[0] <= '9' && !name[1] ? name[0] - '0' : -1;
}
static void *held_row(void *view, void *ctx) {
    char name[2] = { (char)('0' + (int)(long)ctx), 0 };
    return view ? widget_lookup(view, name, 1) : (void *)0; /* its list_item */
}
static int stream_row_again(void *ctx, void *unused) {
    /* in the rows' own view: Home's Library list, beside its Streaming list, names its rows so too
     */
    void *row = held_row(widget_lookup(sw.hold_win, "scroll_view_streamsset", 1), ctx);
    return row ? stock_stream_row_trampoline(row, (void *)0) : 0;
}
static int netservice_row_again(void *ctx, void *unused) {
    void *row = held_row(sw.hold_win, ctx);
    return row ? stock_netservice_row_trampoline(row, (void *)0) : 0;
}
static int online_match_again(void *win, void *unused) {
    return stock_online_match_trampoline(win);
}

/* Streaming's stock row (stream_page_init binds 0x52ee8c): Tidal (0), a login page with the link
 * and msg_notnetwork without; on Streaming's page or Home's Split Streaming list (home.c
 * stream_rows), both waking the radio. ponytail: on Home it runs while Home is still the page in
 * use, as Spotify's row does, the Streaming list gone to another of Home's lists or not; drop it
 * there too if that surprises. */
int ringnav_stream_row(void *button, void *event) {
    int row = row_of(button);
    return row >= 0 && smart_hold(stream_row_again, (void *)(long)row, 0)
               ? 0
               : stock_stream_row_trampoline(button, event);
}

/* Network service's rows (0x4c6728): DLNA (0) and AirPlay (1) switch at once (stock starts their
 * services once the link is up); Wi-Fi transfer (2) opens its page with the link. */
int ringnav_netservice_row(void *button, void *event) {
    return row_of(button) == 2 && smart_hold(netservice_row_again, (void *)2, "netservice_page")
               ? 0
               : stock_netservice_row_trampoline(button, event);
}

/* Now Playing More's Download Cover & Lyrics (0x5281f8, its row's case, given More's window): More
 * itself is no network page, so the radio wakes only for this press. */
int ringnav_online_match(void *win) {
    return win == page_in_use() && smart_hold(online_match_again, win, "playermore_page")
               ? 0
               : stock_online_match_trampoline(win);
}
