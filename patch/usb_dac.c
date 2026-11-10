/* USB DAC for a host that never reads the card: a Nintendo Switch 2 (#290). */
#include "navigation.h"

#define UDC_STATE "/sys/class/udc/13500000.otg/state" /* the X2000's OTG port (otg@0x13500000) */

/* check_usb_status (0x4e91fc), once a second from the idle loop (on_wm_idle_status). A cable in
 * (g_usbdet_value 1) starts the card's storage gadget (storage_config.sh, g_usblink_status 1). The
 * vendor kernel's f_mass_storage sets its pc_check parameter when the host sends the drive a SCSI
 * command, and only that (toolsGetpclinkStatus) takes stock to status 2, where on_wm_timer_dialog
 * opens usbmode_page, which under Audio and DAC swaps the storage gadget for the UAC2 one
 * (uac_device_config.sh). A Switch 2 configures the gadget and never sends it a SCSI command, so
 * pc_check stays 0 and the Q2 stays a drive nobody reads. Under Audio and DAC a host that has
 * configured the gadget is link enough: stock's setter is told so (toolsSetpclinkStatus writes
 * pc_check) and stock takes it from there on this same pass. A charger has no host, so the UDC
 * never reads configured there and a charger still only charges. USB Storage keeps stock's gate: a
 * drive the host never reads loses nothing by waiting.
 * ponytail: "configured" only; a host that suspends the bus right after enumerating reads
 * "suspended" and waits as stock does. */
int ringnav_usb_status(void) {
    if (g_usbmode == 1 && g_usbdet_value == 1 && g_usblink_status == 1) {
        char state[10];
        void *f = fopen(UDC_STATE, "rb");
        int n = f ? (int)fread(state, 1, sizeof state, f) : 0;
        if (f) fclose(f);
        if (n == (int)sizeof state && !memcmp(state, "configured", sizeof state))
            toolsSetpclinkStatus(1);
    }
    return stock_usb_status_trampoline();
}
