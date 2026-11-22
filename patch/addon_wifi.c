/* Card apps that follow the Wi-Fi link (q2app wifi=1, addon.c; docs/internals.md#card-apps): such
 * an app, a VPN for one, starts when the link comes up and stops once the link has been gone
 * ADDON_WIFI_DOWN_MS, so a short drop (a DHCP renew, a roam) leaves it running. Between the two it
 * is never idle-stopped, and it doesn't hold the radio up for Smart Wi-Fi (addon_running), so Smart
 * Wi-Fi's radio off stops it too, and its wake starts it again. A user's Stop, or an app that ended
 * by itself, holds it off until the next link (addon.c wifi_off). Polled once per addon_poll pass.
 *
 * up: the link at the last poll; down_at: when it went (0 while up, or before any link); stopped:
 * this drop's stop is done; read: this link's scan is done; looked_at: when it last asked whether
 * the card is in. A link coming up scans the card first, so an app is found at boot without a visit
 * to Streaming; at boot Wi-Fi can connect half a minute before the card mounts, so the scan waits
 * for the card, asking once a second. ponytail: a link that flaps slower than every
 * ADDON_WIFI_DOWN_MS restarts the apps each time; a longer hold would trade that for a slower stop.
 * ponytail: the connect status follows Wi-Fi's switch and its signal poll, not the association: an
 * app can start a few seconds before the Q2 joins a network, and runs on while Wi-Fi is on with no
 * network in reach; wpa_state COMPLETED (stock's network_checkstatus) would tie it to the
 * association. */
#include "offsets.inc"
#include "peq.h"
#include "stock.h"

#define ADDON_WIFI_DOWN_MS 10000

static struct {
    int up, stopped, read;
    unsigned down_at, looked_at;
} aw __attribute__((section(".scratch")));

void addon_wifi_poll(void) {
    /* stock's connect status: set by the signal poll while Wi-Fi is on (the status bar's bars,
     * get_wifisignal, also need it), 0 once Wi-Fi is off or the link drops */
    int up = networkGetConnectStatus() > 0;
    unsigned now = time_now_ms();
    if (up) {
        if (!aw.up) { /* a new link: the card read again, the user's stops forgotten */
            aw.read = aw.looked_at = 0;
            addon_wifi_release();
        }
        if (!aw.read && (!aw.looked_at || now - aw.looked_at >= 1000)) {
            aw.looked_at = now | !now; /* never 0 */
            if (card_mounted()) {
                addon_scan();
                aw.read = 1;
            }
        }
        aw.up = 1;
        aw.down_at = 0;
        if (addon_wifi_apps()) addon_wifi_start();
        return;
    }
    if (aw.up) {
        aw.up = aw.stopped = 0;
        aw.down_at = now | !now; /* never 0 */
        return;
    }
    if (aw.down_at && !aw.stopped && now - aw.down_at >= ADDON_WIFI_DOWN_MS) {
        addon_wifi_stop();
        aw.stopped = 1;
    }
}
