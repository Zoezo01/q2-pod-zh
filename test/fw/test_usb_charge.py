"""DAC charging policy: run the real usb_charge.c with controlled stock GPIO/configuration calls."""
import ctypes as C

import pytest


class Charging:
    def __init__(self, fw, pref=0, mode=1, cable=0):
        self.fw, self.pref, self.mode, self.cable = fw, pref, mode, cable
        self.gpio, self.writes, self.other = 1, [], []
        self.depth = 0
        self.charge = fw.fn('ringnav_usb_charge', C.c_int, C.c_int)
        self.gpio_write = fw.fn('ringnav_usb_gpio', C.c_int, C.c_char_p, C.c_int)
        self.config = fw.fn('ringnav_usb_config', C.c_int)
        self.detect = fw.fn('ringnav_usb_detect', C.c_int)
        self.status = fw.fn('ringnav_usb_status', C.c_int)
        self.flag = fw.var('g_usbdac_chargeflag', C.c_ubyte)
        self.usbmode = fw.var('g_usbmode', C.c_ubyte)
        fw.mock('stock_usb_gpio_trampoline', self.write)
        fw.mock('stock_usb_config_trampoline', self.load)
        fw.mock('stock_usb_detect_trampoline', lambda *_: self.cable)
        fw.mock('stock_usb_status_trampoline', self.dispatch)
        fw.mock('toolsGpioGetValue', lambda *_: self.gpio)
        fw.mock('fw_pthread_mutex_lock', self.lock)
        fw.mock('fw_pthread_mutex_unlock', self.unlock)

    def lock(self, *_):
        assert self.depth == 0
        self.depth = 1
        return 0

    def unlock(self, *_):
        assert self.depth == 1
        self.depth = 0
        return 0

    def write(self, name, value, *_):
        name = C.string_at(name)
        if name == b'PE22':
            assert self.depth == 1
            self.gpio = value
            self.writes.append(value)
        else:
            self.other.append((name, value))
        return 0

    def load(self, *_):
        assert self.depth == 0  # stock configuration may itself call the guarded writer
        self.flag.value, self.usbmode.value = self.pref, self.mode
        return 77

    def dispatch(self, *_):
        assert self.depth == 0
        self.at_dispatch = self.gpio
        return 55


def test_off_is_asserted_before_config_and_remains_off_through_failed_links_and_reconnects(fw):
    q = Charging(fw)
    q.charge(1)
    assert q.writes == [1]
    assert q.config() == 77
    for cable in (1, 1, 0, 1, 0):
        q.cable = cable
        assert q.detect() == cable
        q.status()
        q.charge(1)  # e.g. the stock disconnect/USB page exit's attempt to restore charging
    assert set(q.writes) == {1}


def test_saved_opt_in_is_allowed_only_after_configuration_has_loaded(fw):
    q = Charging(fw, pref=1, cable=1)

    def load(*_):
        q.load()
        q.charge(1)
        assert q.gpio == 1  # still loading, even though the flag has already been read
        return 77

    fw.mock('stock_usb_config_trampoline', load)
    q.config()
    assert q.writes[0] == 1 and q.gpio == 0


def test_toggle_on_and_off_take_effect_on_the_guarded_write(fw):
    q = Charging(fw, cable=1)
    q.config()
    q.flag.value = 1
    q.charge(1)
    assert q.gpio == 0
    q.flag.value = 0
    q.charge(0)
    assert q.gpio == 1
    q.charge(1)
    assert q.gpio == 1


def test_storage_is_a_non_dac_choice_but_is_inhibited_again_before_dac_dispatch(fw):
    q = Charging(fw, mode=0)
    q.config()
    assert q.gpio == 1  # no cable yet
    q.cable = 1
    q.detect()
    assert q.gpio == 0
    q.usbmode.value = 1
    assert q.status() == 55
    assert q.at_dispatch == 1
    q.usbmode.value = 0
    q.status()
    assert q.at_dispatch == 0
    q.cable = 0
    q.detect()
    assert q.gpio == 1


def test_a_charge_limit_disable_is_not_undone_by_detection_or_gadget_dispatch(fw):
    q = Charging(fw, pref=1)
    q.config()
    q.charge(0)
    for cable in (1, 0, 1):
        q.cable = cable
        q.detect()
        q.status()
        assert q.gpio == 1
    q.charge(1)
    assert q.gpio == 0


def test_direct_pe22_write_cannot_bypass_the_guard_and_radio_gpios_are_untouched(fw):
    q = Charging(fw, cable=1)
    q.config()
    q.gpio_write(b'PE22', 0)
    assert q.gpio == 1
    for name in (b'PD01', b'PD20', b'OTHER'):
        for value in (0, 1):
            q.gpio_write(name, value)
    assert q.other == [(name, value) for name in (b'PD01', b'PD20', b'OTHER') for value in (0, 1)]


def test_readback_mismatch_is_reasserted_without_waiting_for_the_charge_limit_poll(fw):
    q = Charging(fw)
    q.config()
    q.gpio = 0
    before = len(q.writes)
    q.detect()
    assert q.gpio == 1 and len(q.writes) == before + 1


def test_configuration_reload_inhibits_before_reading_new_off_preference(fw):
    q = Charging(fw, pref=1)
    q.config()
    assert q.gpio == 0
    q.pref = 0
    q.writes.clear()
    q.config()
    assert q.writes and set(q.writes) == {1}


@pytest.mark.parametrize('pref,mode,cable', [(2, 1, 1), (255, 1, 1), (0, 2, 1), (0, 255, 1), (0, 0, -1)])
def test_unknown_settings_or_cable_state_do_not_grant_charging(fw, pref, mode, cable):
    q = Charging(fw, pref=pref, mode=mode, cable=cable)
    q.config()
    q.charge(1)
    q.status()
    assert q.writes and set(q.writes) == {1}
