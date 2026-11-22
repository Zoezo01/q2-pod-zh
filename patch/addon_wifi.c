/* Card apps that follow the Wi-Fi link (q2app wifi=1, addon.c; docs/internals.md#card-apps): such
 * an app, a VPN for one, starts when the link comes up and stops once the link has been gone
 * ADDON_WIFI_DOWN_MS, so a short drop (a DHCP renew, a roam) leaves it running. It doesn't hold the
 * radio up for Smart Wi-Fi (addon_running), so Smart Wi-Fi's radio off stops it too, and its wake
 * starts it again. A user's Stop, or an app that ended by itself, holds it off until Wi-Fi is back
 * after being gone ADDON_WIFI_DOWN_MS or more (addon.c wifi_off), so an app should not end its own
 * loop on a network blip. Polled once per addon_poll pass.
 *
 * up: the link at the last poll; down_at: when it went (0 while up, or before any link); stopped:
 * this drop's stop is done; read: this link's scan is done; looked_at: when it last asked whether
 * the card is in. A link coming up scans the card first, so an app is found at boot without a visit
 * to Streaming; at boot Wi-Fi can connect half a minute before the card mounts, so the scan waits
 * for the card, asking once a second. ponytail: a link that flaps slower than every
 * ADDON_WIFI_DOWN_MS restarts the apps each time; a longer hold would trade that for a slower stop.
 * "Up" is the network monitor's connect status (networkGetConnectStatus, +0xd8 of its struct):
 * non-zero while Wi-Fi has an address (networkGetWifiSignal stores 0 when the interface's address
 * is 0.0.0.0, else 1-3 by RSSI), 0 after networkStop or networkDisconnect. ponytail: whether the
 * address is cleared when the access point goes out of range is a device question; if it isn't, the
 * app runs on until Wi-Fi is turned off. */
#include "offsets.inc"
#include "peq.h"
#include "stock.h"

#define ADDON_WIFI_DOWN_MS 10000

static struct {
    int up, stopped, read;
    unsigned down_at, looked_at;
} aw __attribute__((section(".scratch")));

void addon_wifi_poll(void) {
    /* the network monitor's connect status: non-zero while Wi-Fi has an address, 0 after
     * networkStop or networkDisconnect */
    int up = networkGetConnectStatus() > 0;
    unsigned now = time_now_ms();
    if (up) {
        /* a new link (the first after boot, or after a drop that stopped the apps): the card read
         * again, the user's stops forgotten; a blip under ADDON_WIFI_DOWN_MS is the same link */
        if (!aw.up && (aw.stopped || !aw.down_at)) {
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
        addon_wifi_start();
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
