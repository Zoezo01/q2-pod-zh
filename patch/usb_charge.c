/* Apply the saved DAC charging choice before insertion, not only once usbmode_page opens.
 * Every switch_charge_enable call passes here; stock's direct PE22 writes and other GPIOs are not
 * hooked. No enumeration timeout is evidence of a charger: a host rejecting the Q2 looks the same.
 */
#include "navigation.h"

static struct {
    int mutex[6]; /* MIPS glibc's zero-initialized pthread_mutex_t */
    int ready, cable, requested, have_request, applied, have_applied;
    void *row; /* Power's USB DAC Charging label, while that page is built */
} usb_charge __attribute__((section(".scratch")));

static int charge_apply(int force) {
    int allowed =
        usb_charge.ready && (g_usbdac_chargeflag == 1 || (usb_charge.cable == 1 && g_usbmode == 0));
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
 * "1", 0 or 1 kept, anything else 1), written only by the DAC screen's switch. A missing key is On,
 * but the config.ini stock copies in at first boot or a reset says USBDAC_CHARGE=0, so a Q2 nobody
 * ever asked reads Off, the same line the switch writes. Q2POD DACCHARGEOWN 1 marks the key as the
 * user's: until it is there, the choice starts On and the mark is written, so an Off saved after
 * that is kept. ponytail: an Off chosen before this firmware is turned On once; the changelog says
 * so, as no file tells the two apart. */
static void dac_default(void) {
    if (config_value("Q2POD", "DACCHARGEOWN", 2, 0)) return;
    g_usbdac_chargeflag = 1;
    write_int_config(1, "PLAYSET", "USBDAC_CHARGE");
    write_int_config(1, "Q2POD", "DACCHARGEOWN");
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

/* System settings → Power → USB DAC Charging: the DAC screen's own switch (g_usbdac_chargeflag,
 * PLAYSET USBDAC_CHARGE), with no host needed. A toggle applies at once through the guard: it asks
 * for charging as charge_poll would hand it back (on, unless Charge Limit holds it off), and the
 * policy then allows it or not by the new choice. The DAC screen's switch shows here when Power
 * comes back to the front. */
static void dac_text(void) {
    list_toggle(usb_charge.row, "USB DAC Charging", g_usbdac_chargeflag == 1);
}
static int dac_click(void *ctx, void *event) {
    g_usbdac_chargeflag = g_usbdac_chargeflag != 1;
    write_int_config(g_usbdac_chargeflag, "PLAYSET", "USBDAC_CHARGE");
    ringnav_usb_charge(!st.charge_held);
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
