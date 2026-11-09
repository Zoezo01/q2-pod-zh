"""USB DAC for a host that never reads the card (patch/usb_dac.c)."""
import ctypes as C

import pytest

from conftest import Files

UDC_STATE = '/sys/class/udc/13500000.otg/state'
AUDIO_AND_DAC, STORAGE = 1, 0
GADGET_UP, LINKED = 1, 2


@pytest.mark.parametrize('mode, cable, link, state, told', [
    (AUDIO_AND_DAC, 1, GADGET_UP, b'configured\n', True),  # a Switch 2: configured, never read
    (AUDIO_AND_DAC, 1, GADGET_UP, b'not attached\n', False),  # a charger
    (AUDIO_AND_DAC, 1, GADGET_UP, b'suspended\n', False),
    (AUDIO_AND_DAC, 1, GADGET_UP, None, False),  # no UDC file
    (STORAGE, 1, GADGET_UP, b'configured\n', False),  # USB Storage keeps stock's gate
    (AUDIO_AND_DAC, 0, GADGET_UP, b'configured\n', False),  # no cable
    (AUDIO_AND_DAC, 1, LINKED, b'configured\n', False),  # linked already
    (AUDIO_AND_DAC, 1, 0, b'configured\n', False),  # no gadget yet
])
def test_a_configured_host_under_audio_and_dac_is_the_pc_link(fw, mode, cable, link, state, told):
    Files(fw, {UDC_STATE: state} if state is not None else {})
    for name, value in (('g_usbmode', mode), ('g_usbdet_value', cable), ('g_usblink_status', link)):
        fw.var(name, C.c_ubyte).value = value
    fw.mock('stock_usb_status_trampoline', lambda *_: 7)
    assert fw.fn('ringnav_usb_status', C.c_int)() == 7  # stock runs after, every time
    assert [c[1][0] for c in fw.calls('toolsSetpclinkStatus')] == ([1] if told else [])
