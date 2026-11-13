/* Apply the saved DAC charging choice before insertion, not only once usbmode_page opens.
 * All charge-enable and direct PE22 writes pass here; other GPIOs, including the radios', do not.
 * No enumeration timeout is evidence of a charger: a host rejecting the Q2 looks the same. */
#include "navigation.h"

static struct {
    int mutex[6]; /* MIPS glibc's zero-initialized pthread_mutex_t */
    int ready, cable, requested, have_request, applied, have_applied;
} usb_charge __attribute__((section(".scratch")));

static int charge_apply(int force) {
    int allowed =
        usb_charge.ready && (g_usbdac_chargeflag == 1 || (usb_charge.cable == 1 && g_usbmode == 0));
    int enable = allowed && (!usb_charge.have_request || usb_charge.requested);
    if (force || !usb_charge.have_applied || usb_charge.applied != enable ||
        toolsGpioGetValue("PE22") != !enable) {
        int result = stock_usb_gpio_trampoline("PE22", !enable);
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

int ringnav_usb_gpio(const char *name, int value) {
    if (!name || strcmp(name, "PE22")) return stock_usb_gpio_trampoline(name, value);
    /* toolsGpioSetValue chooses output1 for value == 1, output0 otherwise. */
    return ringnav_usb_charge(value != 1);
}

int ringnav_usb_config(void) {
    pthread_mutex_lock(usb_charge.mutex);
    usb_charge.ready = 0;
    charge_apply(1);
    pthread_mutex_unlock(usb_charge.mutex);
    int result = stock_usb_config_trampoline();
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
