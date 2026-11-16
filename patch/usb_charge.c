/* USB DAC Charging: Off, On or Never; every switch_charge_enable call passes the guard here.
 * Stock's direct PE22 writes and other GPIOs are not hooked. No enumeration timeout is evidence
 * of a charger: a host rejecting the Q2 looks the same, so Never blocks a wall adapter too.
 */
#include "navigation.h"

static struct {
    int mutex[6]; /* MIPS glibc's zero-initialized pthread_mutex_t */
    int ready, cable, requested, have_request, applied, have_applied;
    int never; /* Q2POD DACCHARGENEVER: Off held from the cable's first moment, not stock's */
    void *row; /* Power's USB DAC Charging label, while that page is built */
} usb_charge __attribute__((section(".scratch")));

/* Off allows what stock asks: usbmode_page_init asks for g_usbdac_chargeflag once a host links,
 * usbmode_exit for charging again, so a wall adapter charges and a host doesn't. Never, and the
 * settings not read yet, hold it off under Audio and DAC from the cable's first moment, USB
 * Storage aside. None charges while check_otg_status powers a USB device (g_last_otgid: it set
 * PE22 off and its 5 V out on), which the charger would take back as a cable. */
static int charge_apply(int force) {
    int allowed = usb_charge.ready && !g_last_otgid &&
                  (g_usbdac_chargeflag == 1 || !usb_charge.never ||
                   (usb_charge.cable == 1 && g_usbmode == 0));
    int enable = allowed && (!usb_charge.have_request || usb_charge.requested);
    if (force || !usb_charge.have_applied || usb_charge.applied != enable ||
        toolsGpioGetValue("PE22") != !enable) {
        int result = toolsGpioSetValue("PE22", !enable);
        usb_charge.applied = enable;
        usb_charge.have_applied = 1;
        return result;
    }
    return 0;
}

int ringnav_usb_charge(int on) {
    pthread_mutex_lock(usb_charge.mutex);
    usb_charge.requested = on == 1;
    usb_charge.have_request = 1;
    int result = charge_apply(1);
    pthread_mutex_unlock(usb_charge.mutex);
    return result;
}

/* Stock's USB DAC charging choice is PLAYSET USBDAC_CHARGE (config_init: toolsReadConfig's default
 * "1", 0 or 1 kept, anything else 1), which the DAC screen's switch writes; the config.ini stock
 * copies in at first boot or a reset says 0, Off. 1.8.2 to 1.8.6 turned every Q2 On once and
 * marked it Q2POD DACCHARGEOWN 1; mark 2 turns it Off once again, then the key is the user's.
 * ponytail: an On chosen since 1.8.2 is turned Off once, as nothing tells it from the one 1.8.2
 * set; the changelog says so. */
static void dac_default(void) {
    usb_charge.never = config_value("Q2POD", "DACCHARGENEVER", 2, 0);
    if (config_value("Q2POD", "DACCHARGEOWN", 3, 0) == 2) return;
    g_usbdac_chargeflag = 0;
    write_int_config(0, "PLAYSET", "USBDAC_CHARGE");
    write_int_config(2, "Q2POD", "DACCHARGEOWN");
}

int ringnav_usb_config(void) {
    pthread_mutex_lock(usb_charge.mutex);
    usb_charge.ready = 0;
    charge_apply(1);
    pthread_mutex_unlock(usb_charge.mutex);
    int result = stock_usb_config_trampoline();
    dac_default();
    int cable = stock_usb_detect_trampoline();
    pthread_mutex_lock(usb_charge.mutex);
    usb_charge.cable = cable;
    usb_charge.ready = 1;
    charge_apply(1);
    pthread_mutex_unlock(usb_charge.mutex);
    return result;
}

int ringnav_usb_detect(void) {
    int result = stock_usb_detect_trampoline();
    pthread_mutex_lock(usb_charge.mutex);
    usb_charge.cable = result;
    charge_apply(0);
    pthread_mutex_unlock(usb_charge.mutex);
    return result;
}

int ringnav_usb_status(void) {
    /* A mode change must inhibit before stock starts the DAC gadget, not a poll later. */
    pthread_mutex_lock(usb_charge.mutex);
    charge_apply(0);
    pthread_mutex_unlock(usb_charge.mutex);
    return stock_usb_status_trampoline();
}

/* 1 while the charge enable is held off (this guard, or Charge Limit through it): a cable that is
 * in charges nothing then. ponytail: the pin's state, not the charger's; a cable too weak to
 * charge still reads as charging, read /sys/class/power_supply/battery/status if that matters. */
int usb_charge_held(void) { return usb_charge.have_applied && !usb_charge.applied; }

/* What stock would ask of the charger now: the USB DAC or AirPlay page's choice while one is open,
 * else on (charge_poll's hand-back and the row's click). */
int usb_charge_wanted(void) {
    return navigator_window_is_exist("usbmode_page") || navigator_window_is_exist("airplay_page")
               ? g_usbdac_chargeflag
               : 1;
}

/* System settings → Power → USB DAC Charging: Off, On or Never. Off and On are the DAC screen's
 * switch (g_usbdac_chargeflag); Never is Off held from the cable's first moment, for a phone that
 * rejects a DAC drawing charge while it connects. A click applies at once, asking for charging as
 * charge_poll hands it back; Charge Limit's hold still wins. The DAC screen's switch shows here
 * when Power comes back to the front. */
static const char *const dac_names[] = { "Off", "On", "Never" };
static int dac_mode(void) { return g_usbdac_chargeflag == 1 ? 1 : usb_charge.never ? 2 : 0; }
static void dac_text(void) {
    char text[32];
    tk_snprintf(text, sizeof text, "USB DAC Charging: %s", dac_names[dac_mode()]);
    row_label(usb_charge.row, text);
}
static int dac_click(void *ctx, void *event) {
    int mode = (dac_mode() + 1) % 3;
    g_usbdac_chargeflag = mode == 1;
    usb_charge.never = mode == 2;
    write_int_config(g_usbdac_chargeflag, "PLAYSET", "USBDAC_CHARGE");
    write_int_config(usb_charge.never, "Q2POD", "DACCHARGENEVER");
    ringnav_usb_charge(!st.charge_held && usb_charge_wanted());
    dac_text();
    return 0;
}
static int dac_shown(void *ctx, void *event) {
    dac_text();
    return 0;
}
void usb_charge_row(void *win, void *view) {
    if (!view) return;
    usb_charge.row = list_row(view, "usb_chargeswitch", dac_click, 0);
    dac_text();
    widget_on(win, EVT_WINDOW_FOREGROUND, dac_shown, 0);
}
