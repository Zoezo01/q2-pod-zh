/* System settings → Network's card apps (row=network, addon.c): a VPN's row after stock's rows. */
#include "navigation.h"

/* Stock's DLNA, AirPlay and Wi-Fi File Transfer, then card apps whose q2app says row=network, by
 * name, such as a VPN. A Q2 without one shows stock's page as is. Hooked on stock's
 * systemset_netservice_page_init (tools/build.py). */
int ringnav_netservice(void *win, void *ctx) {
    int result = stock_netservice_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_netservice", 1) : (void *)0;
    for (int k = 0, n = view ? addon_network() : 0; k < n; ++k)
        widget_set_text_utf8(
            /* navigation.c list_row_into's settings row, its label clear of the chevron */
            list_row_core(view, addon_network_icon(k), addon_network_open, (void *)(long)k, 220, 1),
            addon_network_name(k));
    return result;
}
