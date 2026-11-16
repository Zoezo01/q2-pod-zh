"""DAC charging policy: run the real usb_charge.c with controlled stock GPIO/configuration calls."""
import ctypes as C

import pytest


class Charging:
    """own: Q2POD DACCHARGEOWN, 2 once the saved choice (pref) is the user's; None, the key missing. never:
    Q2POD DACCHARGENEVER, the guard from the cable's first moment, which most of these tests model."""
    def __init__(self, fw, pref=0, mode=1, cable=0, own=2, never=1):
        self.fw, self.pref, self.mode, self.cable = fw, pref, mode, cable
        self.gpio, self.writes = 1, []
        self.ini = {'DACCHARGENEVER': str(never)} | ({} if own is None else {'DACCHARGEOWN': str(own)})
        self.saved = []
        self.depth = 0
        self.charge = fw.fn('ringnav_usb_charge', C.c_int, C.c_int)
        self.config = fw.fn('ringnav_usb_config', C.c_int)
        self.detect = fw.fn('ringnav_usb_detect', C.c_int)
        self.status = fw.fn('ringnav_usb_status', C.c_int)
        self.flag = fw.var('g_usbdac_chargeflag', C.c_ubyte)
        self.usbmode = fw.var('g_usbmode', C.c_ubyte)
        self.otg = fw.var('g_last_otgid', C.c_ubyte)
        fw.mock('toolsGpioSetValue', self.write)
        fw.mock('stock_usb_config_trampoline', self.load)
        fw.mock('stock_usb_detect_trampoline', lambda *_: self.cable)
        fw.mock('stock_usb_status_trampoline', self.dispatch)
        fw.mock('toolsGpioGetValue', lambda *_: self.gpio)
        fw.mock('fw_pthread_mutex_lock', self.lock)
        fw.mock('fw_pthread_mutex_unlock', self.unlock)
        fw.mock('toolsReadConfig', self.read)
        fw.mock('write_int_config', self.save)

    def read(self, path, section, key, out, default, *_):
        v = self.ini.get(C.string_at(key).decode(), C.string_at(default).decode()).encode()
        C.memmove(out, v + b'\0', len(v) + 1)

    def save(self, value, section, key, *_):
        self.saved.append((C.string_at(section).decode(), C.string_at(key).decode(), C.c_int(value).value))
        self.ini[C.string_at(key).decode()] = str(C.c_int(value).value)

    def lock(self, *_):
        assert self.depth == 0
        self.depth = 1
        return 0

    def unlock(self, *_):
        assert self.depth == 1
        self.depth = 0
        return 0

    def write(self, name, value, *_):
        assert C.string_at(name) == b'PE22' and self.depth == 1
        self.gpio = value
        self.writes.append(value)
        return 0

    def load(self, *_):
        assert self.depth == 0  # stock configuration may itself call the guarded writer
        self.at_config = self.gpio
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


def test_a_pin_left_charging_is_inhibited_while_stock_reads_the_settings_not_only_after(fw):
    q = Charging(fw)
    q.gpio = 0  # charging, as S90play's rockbox() leaves it when demo starts again
    assert q.config() == 77
    assert q.at_config == 1 and q.gpio == 1


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


def test_readback_mismatch_is_reasserted_without_waiting_for_the_charge_limit_poll(fw):
    q = Charging(fw)
    q.config()
    q.gpio = 0
    before = len(q.writes)
    q.detect()
    assert q.gpio == 1 and len(q.writes) == before + 1


@pytest.mark.parametrize('pref,mode,cable', [(2, 1, 1), (255, 1, 1), (0, 2, 1), (0, 255, 1), (0, 0, -1)])
def test_unknown_settings_or_cable_state_do_not_grant_charging(fw, pref, mode, cable):
    q = Charging(fw, pref=pref, mode=mode, cable=cable)
    q.config()
    q.charge(1)
    q.status()
    assert q.writes and set(q.writes) == {1}


@pytest.mark.parametrize('pref,own', [(0, None), (1, None), (1, 1), (0, 1)])  # stock's first boot, no key, 1.8.2's On
def test_a_choice_not_made_since_1_8_6_starts_off_once_then_is_the_users(fw, pref, own):
    q = Charging(fw, pref=pref, cable=1, own=own, never=0)
    q.config()
    assert q.at_config == 1  # still inhibited while stock reads the settings
    assert q.flag.value == 0 and q.gpio == 0  # Off: a charger charges until a host links
    assert q.saved == [('PLAYSET', 'USBDAC_CHARGE', 0), ('Q2POD', 'DACCHARGEOWN', 2)]
    q.pref = 1  # then turned On, and a restart: kept
    q.config()
    assert q.flag.value == 1 and len(q.saved) == 2


def test_a_saved_never_stays_and_inhibits_before_config_and_insertion(fw):
    q = Charging(fw, pref=0)
    q.gpio = 0
    q.config()
    assert q.at_config == 1 and q.flag.value == 0 and q.gpio == 1 and q.saved == []
    q.cable = 1
    q.detect()
    q.status()
    assert q.at_dispatch == 1 and q.gpio == 1


def test_off_charges_from_a_charger_stops_once_a_host_links_and_charges_after_it_leaves(fw):
    q = Charging(fw, cable=1, never=0)
    q.config()
    q.detect()
    q.status()
    assert q.gpio == 0  # a wall adapter: no DAC page, nothing asks it off
    q.charge(q.flag.value)  # usbmode_page_init: switch_charge_enable(g_usbdac_chargeflag)
    for _ in range(3):
        q.detect()
        q.status()
        assert q.gpio == 1
    q.charge(1)  # usbmode_exit
    assert q.gpio == 0


@pytest.mark.parametrize('pref,never,mode', [(1, 0, 1), (0, 0, 1), (0, 0, 0), (0, 1, 0)])
def test_nothing_charges_while_the_q2_powers_a_usb_device(fw, pref, never, mode):
    q = Charging(fw, pref=pref, never=never, mode=mode, cable=1)
    q.config()
    assert q.gpio == 0
    q.otg.value, q.gpio = 1, 1  # check_otg_status: 5 V out on, PE22 off
    before = len(q.writes)
    for _ in range(3):
        q.detect()  # the Q2's own 5 V reads as a cable
        q.status()
        q.charge(1)  # Charge Limit's hand-back
        assert q.gpio == 1
    assert set(q.writes[before:]) <= {1}
    q.otg.value, q.gpio = 0, 0  # the device out: stock releases PE22
    q.status()
    assert q.gpio == 0
