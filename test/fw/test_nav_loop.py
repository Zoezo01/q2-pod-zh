"""patch/navigation.c's UI loop and key path, with system.c's Standby limit and boot logo and ratings.c's card copy of
the ratings: Rockbox's hand-over, Bluetooth's absolute volume, the loop's pacing, the input gates, Key Tone, the
setting editors and Classic's Set Time, and the key release's list navigation (ringnav)."""
import calendar
import ctypes as C
import random
import re
import struct

import pytest

import host
from conftest import NAVIGATION_SOURCE, O, Files, claim

KEY_CENTER, KEY_PREV, KEY_NEXT, KEY_PLAY, KEY_RETURN = (O[k] for k in ('KEY_CENTER', 'KEY_PREV', 'KEY_NEXT', 'KEY_PLAY', 'KEY_RETURN'))
KEY_BACK_BTN, KEY_FWD_BTN = O['KEY_BACK_BTN'], O['KEY_FWD_BTN']
DOWN, UP, LONG = O['EVT_KEY_DOWN'], O['EVT_KEY_UP'], O['EVT_KEY_LONG']
STOP = 11
REC_ID, REC_PATH, REC_ALBUM, REC_ALBUM_ARTIST, REC_CUE_START = (O[k] for k in ('REC_ID', 'REC_PATH', 'REC_ALBUM', 'REC_ALBUM_ARTIST', 'REC_CUE_START'))
NAVIGATION_C = NAVIGATION_SOURCE


def define(name):
    """A number navigation.h or a unit of it #defines for itself (offsets.inc doesn't carry it): the watchdog's are
    system.c's."""
    return int(host.tools_build.need(re.search(r'#define %s (\w+)' % name, NAVIGATION_C), 'no %s in navigation.h or its units' % name)[1], 0)


WDT_START, WDT_STOP, WDT_FEED = (define(k) for k in ('WDT_START', 'WDT_STOP', 'WDT_FEED'))  # the watchdog's ioctls
SHORT_LIST_MAX, LIST_FIRST_MS = define('SHORT_LIST_MAX'), define('LIST_FIRST_MS')


@pytest.fixture(scope='module')
def scratch(library):
    """scratch_t's field offsets as this build lays it out: its pointers move them."""
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


@pytest.fixture
def st(fw, scratch):
    base = C.addressof(fw.var('st', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + scratch[name])


@pytest.fixture(scope='module')
def power_fields(library):
    """system.c's own state, pw: its field offsets as this build lays it out."""
    return host.offsets(library.parent/'system.o', 'pw')


@pytest.fixture
def power(fw, power_fields):
    base = C.addressof(fw.var('system__pw', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + power_fields[name])


class Config:
    """config.ini: a key's value, or the default stock passes."""
    def __init__(self, fw, **values):
        self.values = {k.encode(): v for k, v in values.items()}
        fw.mock('toolsReadConfig', self.read)

    def read(self, path, section, key, out, default, *_):
        value = self.values.get(C.string_at(key), C.string_at(default))
        C.memmove(out, value + b'\0', len(value) + 1)


class Clock:
    """time_now_ms, moved by the test."""
    def __init__(self, fw, now=100000):
        self.now = now
        fw.mock('time_now_ms', lambda *_: self.now)


def toasts(fw):
    seen = []
    fw.mock('navigator_to_with_context', lambda name, info, *_: seen.append(C.string_at(info + 8)) and 0)
    return seen


@pytest.fixture
def stock_bytes():
    """Stock's fixed addresses a test writes, put back after: conftest's map outlives a load."""
    saved = []
    def at(address, size=4):
        claim(address)
        saved.append((address, C.string_at(address, size)))
        C.memset(address, 0, size)
        return address
    yield at
    for address, data in reversed(saved):
        C.memmove(address, data, len(data))


# Standby limit (docs/internals.md#standby-limit).
RTC_NOW, RTC_ALARM, STATE = '/sys/class/rtc/rtc0/since_epoch', '/sys/class/rtc/rtc0/wakealarm', '/mnt/data/q2-state'
LIMITS = [0, O['STANDBY_LIMIT_1'], O['STANDBY_LIMIT_2'], O['STANDBY_LIMIT_3']]


class Rtc(Files):
    """The RTC in sysfs: since_epoch its seconds, wakealarm an alarm as text, which the kernel refuses
    (EBUSY, at the close's write) while another is set; 0 clears it."""
    def __init__(self, fw, now=1_700_000_000, clock=True):
        super().__init__(fw)
        self.clock, self.alarm, self.writes, self.refuse = clock, 0, [], False
        self.now = now

    @property
    def now(self):
        return self._now

    @now.setter
    def now(self, value):
        self._now = value
        if self.clock:
            self.files[RTC_NOW] = b'%d\n' % value

    def fclose(self, f, *_):
        path, _, written = self.open[f]
        if path != RTC_ALARM:
            return super().fclose(f)
        del self.open[f]
        at = int(written)
        self.writes.append(at)
        if self.refuse or (at and self.alarm):
            return -1
        self.alarm = at
        return 0


def standby(fw, rtc, limit, slept, result=7):
    """ringnav_standby with Standby limit's choice, the suspend lasting slept seconds."""
    Config(fw, STANDBYLIMIT=b'%d' % limit)
    seen = {}
    def suspend(*_):
        seen['state'] = rtc.files.get(STATE)
        rtc.now += slept
        return result
    fw.mock('stock_standby_trampoline', suspend)
    return fw.fn('ringnav_standby', C.c_int)(), seen


@pytest.mark.parametrize('limit', [1, 2, 3])
def test_standby_sets_the_alarm_its_limit_ahead_and_clears_it_after(fw, st, limit):
    rtc = Rtc(fw)
    start = rtc.now
    assert standby(fw, rtc, limit, 60)[0] == 7
    assert rtc.writes == [0, start + LIMITS[limit] * 3600, 0] and rtc.alarm == 0


@pytest.mark.parametrize('slept, off', [(LIMITS[1] * 3600, 1), (LIMITS[1] * 3600 + 1, 1), (LIMITS[1] * 3600 - 1, 0), (10, 0)])
def test_only_a_resume_at_the_alarm_powers_off(fw, power, slept, off):
    standby(fw, Rtc(fw), 1, slept)
    assert power('standby_off').value == off
    shown = []
    fw.mock('navigator_to', lambda name, *_: shown.append(C.string_at(name)) and 0)
    fw.fn('system__standby_poll', None)()
    assert shown == ([b'poweroff_page'] if off else []) and power('standby_off').value == 0


@pytest.mark.parametrize('cable, status', [(1, 1), (0, 2)])
def test_a_cable_or_music_keeps_the_q2_on_after_the_alarm(fw, power, cable, status):
    standby(fw, Rtc(fw), 1, LIMITS[1] * 3600)
    fw.var('g_usbdet_value', C.c_ubyte).value = cable
    fw.mock('mclGetPlayStatus', lambda *_: status)
    fw.fn('system__standby_poll', None)()
    assert not fw.calls('navigator_to') and power('standby_off').value == 0  # the next Standby counts afresh


def test_without_a_limit_standby_is_stocks(fw, power):
    rtc = Rtc(fw)
    assert standby(fw, rtc, 0, 99999, result=-3)[0] == -3
    assert rtc.writes == [] and power('standby_off').value == 0 and len(fw.calls('stock_standby_trampoline')) == 1


def test_an_unreadable_clock_leaves_standby_stocks(fw, power):
    rtc = Rtc(fw, clock=False)
    standby(fw, rtc, 3, 99999)
    assert rtc.writes == [] and power('standby_off').value == 0


def test_an_alarm_the_rtc_refuses_never_powers_off(fw, power):
    rtc = Rtc(fw)
    rtc.refuse = True
    standby(fw, rtc, 1, 99999)
    assert rtc.writes == [0] and power('standby_off').value == 0  # the clear failed: nothing armed


def test_an_alarm_left_set_is_cleared_first(fw, power):
    rtc = Rtc(fw)
    rtc.alarm = 123
    standby(fw, rtc, 1, LIMITS[1] * 3600)
    assert power('standby_off').value == 1


def test_the_suspend_is_recorded_and_the_watchdog_sleeps_through_it(fw, power):
    rtc = Rtc(fw)
    power('wdt').value = 9
    _, seen = standby(fw, rtc, 1, 10)
    assert b'power:suspending' in seen['state']
    ioctls = [(c[1][0], c[1][1] & 0xffffffff) for c in fw.calls('fw_ioctl')]
    assert ioctls == [(9, WDT_STOP), (9, WDT_START)]  # stopped before, started after
    assert power('suspended').value == 0
    fw.fn('system__state_poll', None)()
    assert b'power:running' in rtc.files[STATE]


# The boot logo (docs/boot.md).
LOGO_SRC, LOGO_DST, LOGO_ROT = '/mnt/mmc/Q2Pod/boot/boot-logo.jpg', '/mnt/data/boot-logo.jpg', '/tmp/boot-logo.jpg'


def jpeg(w, h, marker=0xc0, bits=8, parts=3, before=b'', tag=b''):
    """A JPEG's header as far as the frame: SOI, JFIF, any segments before, the frame, an empty scan."""
    app0 = b'\xff\xe0' + struct.pack('>H', 16) + b'JFIF\0\1\1\0\0\1\0\1\0\0'
    com = b'\xff\xfe' + struct.pack('>H', 2 + len(tag)) + tag if tag else b''
    frame = b'\xff' + bytes([marker]) + struct.pack('>HBHHB', 8 + 3 * parts, bits, h, w, parts)
    frame += b''.join(bytes([i + 1, 0x11, 0]) for i in range(parts))
    return b'\xff\xd8' + app0 + com + before + frame + b'\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00' + b'\xff\xd9'


def jpeg_size(fw, data):
    block = C.create_string_buffer(data, len(data) or 1)
    return fw.fn('system__jpeg_size', C.c_uint, C.c_void_p, C.c_uint)(block, len(data))


@pytest.mark.parametrize('w, h', [(320, 375), (375, 320), (1, 1), (0xffff, 0xffff)])
def test_jpeg_size_is_the_frames_width_and_height(fw, w, h):
    assert jpeg_size(fw, jpeg(w, h)) == w << 16 | h


@pytest.mark.parametrize('kind', [dict(marker=0xc2), dict(marker=0xc1), dict(bits=12), dict(parts=1), dict(parts=4)])
def test_jpeg_size_refuses_what_display_logo_cant_draw(fw, kind):
    assert jpeg_size(fw, jpeg(320, 375, **kind)) == 0  # progressive, extended, 12-bit, grey, CMYK


@pytest.mark.parametrize('w, h', [(0, 375), (320, 0)])
def test_jpeg_size_refuses_an_empty_frame(fw, w, h):
    assert jpeg_size(fw, jpeg(w, h)) == 0


def test_jpeg_size_passes_fill_bytes_restarts_and_tables(fw):
    dht = b'\xff\xc4' + struct.pack('>H', 5) + b'\0\0\0'  # a table, in the frame markers' range
    assert jpeg_size(fw, jpeg(320, 375, before=b'\xff\xff\xff\xd0\xff\x01' + dht)) == 320 << 16 | 375


@pytest.mark.parametrize('data', [b'', b'\xff', b'\xff\xd8', b'\x89PNG\r\n', b'\xff\xd8\xff\xda\0\2', b'\xff\xd8\xff\xd9',
                                  b'\xff\xd8\xff\xd8', b'\xff\xd8\xff\x00', b'\xff\xd8\x00\xff\xc0', b'\xff\xd8\xff\xff'])
def test_jpeg_size_of_no_frame_is_zero(fw, data):
    assert jpeg_size(fw, data) == 0


def test_jpeg_size_agrees_with_the_builders_check(fw):
    """tools/build.py's jpeg_size checks a --logo the same way: its answer, any bytes."""
    rng = random.Random(0x10e0)
    seeds = [jpeg(320, 375), jpeg(375, 320, tag=b'x' * 9), jpeg(320, 375, before=b'\xff\xd3'), jpeg(5, 7, parts=1)]
    for _ in range(3000):
        data = bytearray(rng.choice(seeds))
        for _ in range(rng.randrange(4)):
            if not data:
                break
            at = rng.randrange(len(data))
            kind = rng.randrange(3)
            if kind == 0:
                data[at] = rng.choice([0, 0xff, 0xc0, 0xd9, 0xda, 1, 0xd0, rng.randrange(256)])
            elif kind == 1:
                del data[at:]
            else:
                data[at:at] = bytes([0xff] * rng.randrange(1, 3))
        try:
            w, h = host.tools_build.jpeg_size(bytes(data))
            want = w << 16 | h
        except (ValueError, IndexError, struct.error):
            want = 0
        assert jpeg_size(fw, bytes(data)) == want, bytes(data)


class Card(Files):
    """The card and flash as files, access() and stat() too (o32: size word 12, mtime word 16); ffmpeg as a child
    that writes rot (or fails)."""
    def __init__(self, fw, files=None):
        super().__init__(fw, files)
        self.rot, self.status, self.pid, self.mtime = None, 0, 4321, {}
        fw.mock('fw_access', lambda path, *_: 0 if C.string_at(path).decode() in self.files else -1)
        fw.mock('fw___xstat', self.stat)
        fw.mock('fw_fork', lambda *_: self.pid)
        fw.mock('fw_waitpid', self.wait)

    def stat(self, version, path, info, *_):
        path = C.string_at(path).decode()
        if path not in self.files:
            return -1
        words = (C.c_uint * 36).from_address(info)
        words[12], words[16] = len(self.files[path]), self.mtime.get(path, 1)
        return 0

    def wait(self, pid, status, *_):
        if self.rot is not None:
            self.files[LOGO_ROT] = self.rot
        C.c_int.from_address(status).value = self.status
        return pid


def logo_sync(fw, power):
    fw.fn('system__logo_sync', C.c_void_p, C.c_void_p)(None)
    err = power('logo_err', C.c_void_p).value
    return C.string_at(err) if err else None


def test_no_card_logo_brings_the_stock_logo_back(fw, power):
    card = Card(fw, {LOGO_DST: jpeg(320, 375)})
    power('logo_busy').value = 1
    assert logo_sync(fw, power) is None and LOGO_DST not in card.files and power('logo_busy').value == 0


def test_a_portrait_logo_is_kept_on_flash(fw, power):
    logo = jpeg(320, 375, tag=b'mine')
    card = Card(fw, {LOGO_SRC: logo, LOGO_DST: jpeg(320, 375, tag=b'old')})
    assert logo_sync(fw, power) is None and card.files[LOGO_DST] == logo
    assert not fw.calls('fw_fork') and LOGO_DST + '.tmp' not in card.files


def test_the_same_logo_is_not_written_again(fw, power):
    logo = jpeg(320, 375)
    card = Card(fw, {LOGO_SRC: logo, LOGO_DST: logo})
    assert logo_sync(fw, power) is None and card.files[LOGO_DST] == logo
    assert [C.string_at(c[1][1]) for c in fw.calls('fw_fopen')] == [b'rb', b'rb']  # read both, wrote nothing


def test_a_landscape_logo_is_turned_upright_by_ffmpeg(fw, power):
    card = Card(fw, {LOGO_SRC: jpeg(375, 320)})
    card.rot = jpeg(320, 375, tag=b'turned')
    assert logo_sync(fw, power) is None and card.files[LOGO_DST] == card.rot
    assert LOGO_ROT not in card.files  # the temporary goes
    assert fw.calls('fw_waitpid')[0][1][0] == card.pid


@pytest.mark.parametrize('status, rot', [(1 << 8, jpeg(320, 375)), (0, None), (0, jpeg(375, 320))])
def test_a_failed_turn_keeps_the_old_logo_and_says_so(fw, power, status, rot):
    old = jpeg(320, 375, tag=b'old')
    card = Card(fw, {LOGO_SRC: jpeg(375, 320), LOGO_DST: old})
    card.status, card.rot = status, rot
    assert logo_sync(fw, power) and card.files[LOGO_DST] == old


def test_no_fork_keeps_the_old_logo(fw, power):
    old = jpeg(320, 375, tag=b'old')
    card = Card(fw, {LOGO_SRC: jpeg(375, 320), LOGO_DST: old})
    card.pid = -1
    assert logo_sync(fw, power) and card.files[LOGO_DST] == old and not fw.calls('fw_waitpid')


def test_the_child_runs_ffmpeg_on_the_card_logo(fw, power):
    card = Card(fw, {LOGO_SRC: jpeg(375, 320)})
    card.pid = 0
    logo_sync(fw, power)
    assert [C.string_at(c[1][0]) for c in fw.calls('fw_execl')] == [b'/usr/bin/ffmpeg']  # stock's ffmpeg
    assert [c[1][0] for c in fw.calls('child_exit')] == [127]  # exec failed: the child ends
    assert C.string_at(fw.calls('fw_open')[0][1][0]) == b'/proc/self/oom_score_adj'  # the OOM killer's first


@pytest.mark.parametrize('logo', [jpeg(320, 376), jpeg(640, 750), jpeg(320, 375, marker=0xc2), b'not a jpeg', b'\xff\xd8' + b'\0' * (512 * 1024)])
def test_a_logo_of_another_kind_is_refused_and_the_old_one_kept(fw, power, logo):
    old = jpeg(320, 375, tag=b'old')
    card = Card(fw, {LOGO_SRC: logo, LOGO_DST: old})
    assert logo_sync(fw, power) and card.files[LOGO_DST] == old and not fw.calls('fw_fork')


def test_a_save_that_fails_says_so_differently_and_leaves_no_tmp(fw, power):
    card = Card(fw, {LOGO_SRC: jpeg(320, 376)})
    refused = logo_sync(fw, power)
    card.files[LOGO_SRC], card.fail_rename = jpeg(320, 375), True
    failed = logo_sync(fw, power)
    assert failed and failed != refused and LOGO_DST + '.tmp' not in card.files and LOGO_DST not in card.files


class Mounts:
    """/proc/mounts with the card in or out; pthread_create runs the thread there and then, or fails."""
    def __init__(self, fw, card, mounted=True):
        self.mounted, self.fail, self.started = mounted, False, []
        card.files['/proc/mounts'] = b''
        self.card = card
        self.set(mounted)
        fw.mock('fw_pthread_create', self.create)

    def set(self, mounted):
        self.mounted = mounted
        self.card.files['/proc/mounts'] = (b'/dev/root / squashfs ro 0 0\n' +
                                           (b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n' if mounted else b'') +
                                           b'tmpfs /tmp tmpfs rw 0 0\n')

    def create(self, tid, attr, start, arg, *_):
        self.started.append(start)
        if self.fail:
            return 11
        C.CFUNCTYPE(C.c_void_p, C.c_void_p)(start)(arg)
        return 0


def logo_poll(fw):
    fw.fn('system__logo_poll', None)()


def test_the_card_mounting_lit_syncs_the_logo(fw, power):
    clock = Clock(fw)
    card = Card(fw, {LOGO_SRC: jpeg(320, 375)})
    mounts = Mounts(fw, card)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    logo_poll(fw)
    assert mounts.started == [C.cast(fw.lib.system__logo_sync, C.c_void_p).value]
    assert card.files[LOGO_DST] == card.files[LOGO_SRC] and power('logo_busy').value == 0
    assert C.c_int.in_dll(fw.lib, 'ratings__rc_due').value == 1  # another card: its ratings copy is due
    clock.now += O['CHARGE_POLL_MS']
    logo_poll(fw)
    assert len(mounts.started) == 1  # still the same mount


def test_dark_the_mount_waits_for_the_screen(fw, st):
    Clock(fw)
    mounts = Mounts(fw, Card(fw, {}))
    logo_poll(fw)
    assert mounts.started == []
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    logo_poll(fw)
    assert len(mounts.started) == 1


def test_the_card_is_looked_at_every_charge_poll(fw, st):
    clock = Clock(fw)
    card = Card(fw, {})
    mounts = Mounts(fw, card, mounted=False)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    logo_poll(fw)
    mounts.set(True)
    clock.now += O['CHARGE_POLL_MS'] - 1
    logo_poll(fw)
    assert mounts.started == []
    clock.now += 1
    logo_poll(fw)
    assert len(mounts.started) == 1
    mounts.set(False)
    clock.now += O['CHARGE_POLL_MS']
    logo_poll(fw)
    mounts.set(True)  # USB storage's end mounts it again
    clock.now += O['CHARGE_POLL_MS']
    logo_poll(fw)
    assert len(mounts.started) == 2


def test_a_thread_that_cant_start_is_tried_again(fw, power):
    clock = Clock(fw)
    mounts = Mounts(fw, Card(fw, {}))
    mounts.fail = True
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    logo_poll(fw)
    assert power('logo_busy').value == 0 and power('logo_mounted').value == 0
    mounts.fail = False
    clock.now += O['CHARGE_POLL_MS']
    logo_poll(fw)
    assert len(mounts.started) == 2 and power('logo_mounted').value == 1


def test_a_running_sync_holds_the_poll(fw, power):
    Clock(fw)
    mounts = Mounts(fw, Card(fw, {}))
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    power('logo_busy').value = 1
    logo_poll(fw)
    assert mounts.started == [] and not fw.calls('fw_fopen')


def test_the_syncs_failure_is_toasted_once(fw, power):
    clock = Clock(fw)
    seen = toasts(fw)
    card = Card(fw, {LOGO_SRC: b'not a jpeg'})
    Mounts(fw, card)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    logo_poll(fw)
    err = power('logo_err', C.c_void_p).value
    assert seen == [] and err  # the thread's; the next pass on the UI thread shows it
    clock.now += 1
    logo_poll(fw)
    clock.now += 1
    logo_poll(fw)
    assert seen == [C.string_at(err)] and not power('logo_err', C.c_void_p).value


LOGO_BAD = 'Boot logo must be a 320x375 JPEG'


def power_on(fw, power, clock):
    """A power-on's polls, lit with the card mounted: what was toasted. A toast's dialog runs the UI loop, which polls
    again inside it, so a message still set would toast inside its own toast: three levels are run, not for ever."""
    for field in ('logo_at', 'logo_mounted'):
        power(field).value = 0  # a fresh boot's state (.scratch starts zeroed)
    seen, depth = [], [0]
    def toast(name, info, *_):
        seen.append(C.string_at(info + 8).decode())
        if depth[0] < 3:
            depth[0] += 1
            clock.now += O['CHARGE_POLL_MS']
            logo_poll(fw)
            depth[0] -= 1
        return 0
    fw.mock('navigator_to_with_context', toast)
    for _ in range(4):
        clock.now += O['CHARGE_POLL_MS']
        logo_poll(fw)
    return seen


# an invalid card logo: its message once, boot goes on (a detached thread), the old logo kept; each later power-on
# with the same file says nothing, and a changed file is reported again
def test_an_invalid_card_logo_says_so_once_and_boot_goes_on(fw, power):
    clock = Clock(fw)
    old = jpeg(320, 375, tag=b'old')
    card = Card(fw, {LOGO_SRC: jpeg(640, 480), LOGO_DST: old})
    mounts = Mounts(fw, card)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    for boot in range(3):
        assert power_on(fw, power, clock) == ([LOGO_BAD] if boot == 0 else []), boot  # once, not each power-on
        assert card.files[LOGO_DST] == old and power('logo_busy').value == 0 and not power('logo_err', C.c_void_p).value
    assert len(mounts.started) == 3 and len(fw.calls('fw_pthread_detach')) == 3  # one sync a power-on, detached
    card.mtime[LOGO_SRC] = 2  # saved again: another file
    assert power_on(fw, power, clock) == [LOGO_BAD] and power_on(fw, power, clock) == []


# a stat that fails (the card pulled) records nothing: the file is reported at each power-on, never filed as {0, 0}
def test_a_failed_stat_records_nothing(fw, power):
    clock = Clock(fw)
    card = Card(fw, {LOGO_SRC: jpeg(640, 480)})
    Mounts(fw, card)
    fw.mock('fw___xstat', lambda *_: -1)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    assert [power_on(fw, power, clock) for _ in range(2)] == [[LOGO_BAD]] * 2
    assert '/mnt/data/boot-logo.bad' not in card.files


# a good logo or none at a power-on between forgets the refused file: the same bad file put back is reported again
@pytest.mark.parametrize('between', [jpeg(320, 375), None], ids=['good', 'none'])
def test_a_good_logo_or_none_between_lets_the_bad_one_be_reported_again(fw, power, between):
    clock = Clock(fw)
    bad = jpeg(640, 480)
    card = Card(fw, {LOGO_SRC: bad})
    Mounts(fw, card)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    assert power_on(fw, power, clock) == [LOGO_BAD]
    if between is None:
        del card.files[LOGO_SRC]
    else:
        card.files[LOGO_SRC] = between
    assert power_on(fw, power, clock) == []
    card.files[LOGO_SRC] = bad
    assert power_on(fw, power, clock) == [LOGO_BAD]


@pytest.mark.parametrize('mounts, mounted', [(b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n', 1), (b'/dev/sda1 /mnt/mmc2 vfat rw 0 0\n', 0),
                                             (b'x /mnt/mmc/a vfat\n', 0), (b'', 0), (None, 0)])
def test_card_mounted_reads_proc_mounts(fw, mounts, mounted):
    Files(fw, {} if mounts is None else {'/proc/mounts': mounts})
    assert fw.fn('card_mounted', C.c_int)() == mounted


# The card's copy of the ratings (docs/internals.md#ratings).
RC_FILE = '/mnt/mmc/.ratings.tsv'
RC_HEADER = b'#song\thearts\tpath\tcue_start\n#album\thearts\tname\talbum_artist\tfolder\n'
MOUNTED = b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n'


class Rc(C.Structure):
    _fields_ = [('db', C.c_void_p), ('song', C.c_void_p), ('album', C.c_void_p),
                *((k, C.c_uint) for k in 'songs albums len cap at'.split()), ('out', C.c_void_p),
                *((k, C.c_int) for k in 'table last rows oom'.split()), ('gen', C.c_uint)]


def record(keep, path=None, album=None, artist=None, cue=0, rid=0):
    """A library record as the copy builds one from a row: path, album, album artist, CUE start."""
    r = C.create_string_buffer(0x60)
    for off, s in ((REC_PATH, path), (REC_ALBUM, album), (REC_ALBUM_ARTIST, artist)):
        if s is not None:
            keep.append(C.create_string_buffer(s))
            struct.pack_into('<Q', r, off, C.addressof(keep[-1]))
    struct.pack_into('<i', r, REC_CUE_START, cue)
    struct.pack_into('<i', r, REC_ID, rid)
    keep.append(r)
    return r


class Library:
    """The ratings, kept by the firmware's own rate_keep, and stock's database as the copy queries it:
    myfav and songtable rows (id, fileurl, time, album, albumsonger), sqlite_master knowing songtable."""
    ROW = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.POINTER(C.c_char_p), C.POINTER(C.c_char_p))

    def __init__(self, fw, files=None):
        self.fw, self.keep = fw, []
        self.card = Files(fw, files if files is not None else {})
        self.card.files['/proc/mounts'] = MOUNTED
        self.clock = Clock(fw)
        self.myfav, self.songs, self.scanned, self.sql = [], [], True, []
        fw.mock('sqlite3_open', self.open_db)
        fw.mock('sqlite3_exec', self.exec_db)
        self.keep_fn = fw.fn('rate_keep', C.c_int, C.c_int, C.c_uint, C.c_int)
        self.listen_key = fw.fn('listen_key', C.c_uint, C.c_void_p)
        self.album_id = fw.fn('album_id', C.c_uint, C.c_void_p)
        self.poll = fw.fn('ratings_copy_poll', None)

    def open_db(self, path, out, *_):
        C.c_void_p.from_address(out).value = 0x5150
        return 0

    def exec_db(self, db, sql, row, ctx, err, *_):
        q = C.string_at(sql).decode()
        self.sql.append(q)
        if q.startswith('PRAGMA'):
            return 0
        if 'sqlite_master' in q:
            rows = [(b'1',)] if self.scanned else []
        else:
            last, limit = int(q.split('id>')[1].split()[0]), int(q.split('limit ')[1])
            table = self.myfav if ' from myfav ' in q else self.songs
            rows = [r for r in table if int(r[0]) > last][:limit]
        call = self.ROW(row)
        for r in rows:
            values = (C.c_char_p * len(r))(*r)
            if call(ctx, len(r), values, None):
                return 4  # SQLITE_ABORT
        return 0

    def song(self, path, album=b'', artist=b'', cue=0, table=None):
        table = self.songs if table is None else table
        table.append((b'%d' % (len(table) + 1), path, b'%d' % cue, album, artist))

    def rate(self, path, hearts, cue=0):
        assert self.keep_fn(0, self.listen_key(record(self.keep, path, cue=cue)), hearts) == 1

    def rate_album(self, album, artist, path, hearts):
        assert self.keep_fn(1, self.album_id(record(self.keep, path, album, artist)), hearts) == 1

    def rc(self):
        return self.fw.var('ratings__rc', Rc)

    def run(self, polls=50):
        """The UI loop's passes, a second apart, until a pass has started and ended."""
        started = False
        for _ in range(polls):
            self.clock.now += 1000
            self.poll()
            started |= bool(self.rc().db)
            if started and not self.rc().db:
                return True
        return False

    def copy(self):
        return self.card.files.get(RC_FILE)


def test_the_card_copy_names_each_rated_song_and_album(fw, st):
    lib = Library(fw)
    lib.song(b'/mnt/mmc/Fav/f.flac', table=lib.myfav)
    lib.song(b'/mnt/mmc/A/1.flac', b'Album', b'Artist')
    lib.song(b'/mnt/mmc/A/2.flac', b'Album', b'Artist')
    lib.song(b'/mnt/mmc/B/3.flac', b'Other', b'Band')
    lib.rate(b'/mnt/mmc/A/2.flac', 3)
    lib.rate_album(b'Album', b'Artist', b'/mnt/mmc/A/1.flac', 4)
    assert lib.run()
    assert lib.copy() == RC_HEADER + (b'song\t5\t/mnt/mmc/Fav/f.flac\t\n'  # My Fav: 5 hearts
                                      b'album\t4\tAlbum\tArtist\t\n'        # the album, at its first song
                                      b'song\t3\t/mnt/mmc/A/2.flac\t\n')


def test_a_cue_track_keeps_its_start(fw, st):
    lib = Library(fw)
    lib.song(b'/mnt/mmc/live.flac', cue=0)
    lib.song(b'/mnt/mmc/live.flac', cue=245)
    lib.rate(b'/mnt/mmc/live.flac', 2, cue=245)
    assert lib.run() and lib.copy() == RC_HEADER + b'song\t2\t/mnt/mmc/live.flac\t245\n'


def test_an_album_without_its_artist_is_named_by_its_folder(fw, st):
    lib = Library(fw)
    lib.song(b'/mnt/mmc/Disc Set/CD2/t.flac', b'Set', b'')
    lib.rate_album(b'Set', b'', b'/mnt/mmc/Disc Set/CD2/t.flac', 5)
    assert lib.run() and lib.copy() == RC_HEADER + b'album\t5\tSet\t\t/mnt/mmc/Disc Set\n'  # disc folders as one


def test_the_unknown_album_card_is_one_album(fw, st):
    lib = Library(fw)
    assert lib.keep_fn(1, lib.album_id(record(lib.keep, rid=-1)), 2) == 1
    lib.song(b'/mnt/mmc/x.flac')
    assert lib.run() and lib.copy() == RC_HEADER + b'album\t2\tUnknown Album\t\t\n'


def test_tabs_and_newlines_in_a_name_become_spaces(fw, st):
    lib = Library(fw)
    lib.song(b'/mnt/mmc/a\tb\nc.flac', b'X\tY', b'Z\r')
    lib.rate(b'/mnt/mmc/a\tb\nc.flac', 1)
    lib.rate_album(b'X\tY', b'Z\r', b'/mnt/mmc/a\tb\nc.flac', 3)
    assert lib.run()
    assert lib.copy() == RC_HEADER + b'song\t1\t/mnt/mmc/a b c.flac\t\nalbum\t3\tX Y\tZ \t\n'


def test_a_rating_whose_song_left_the_library_stays_out(fw, st):
    lib = Library(fw)
    lib.song(b'/mnt/mmc/here.flac')
    lib.rate(b'/mnt/mmc/here.flac', 4)
    lib.rate(b'/mnt/mmc/gone.flac', 2)
    assert lib.run() and lib.copy() == RC_HEADER + b'song\t4\t/mnt/mmc/here.flac\t\n'


def test_before_the_first_scan_my_fav_alone(fw, st):
    lib = Library(fw)
    lib.scanned = False
    lib.song(b'/mnt/mmc/f.flac', table=lib.myfav)
    lib.rate(b'/mnt/mmc/r.flac', 3)
    assert lib.run() and lib.copy() == RC_HEADER + b'song\t5\t/mnt/mmc/f.flac\t\n'
    assert not [q for q in lib.sql if 'from songtable where' in q]


def test_pages_of_64_rows_walk_the_whole_library(fw, st):
    lib = Library(fw)
    for i in range(200):
        lib.song(b'/mnt/mmc/%03d.flac' % i)
    lib.rate(b'/mnt/mmc/199.flac', 2)
    assert lib.run()
    pages = [q for q in lib.sql if 'from songtable where' in q]
    assert [int(q.split('id>')[1].split()[0]) for q in pages] == [0, 64, 128, 192]
    assert lib.copy() == RC_HEADER + b'song\t2\t/mnt/mmc/199.flac\t\n'


def test_the_pass_stops_paging_once_every_rating_is_found(fw, st):
    lib = Library(fw)
    for i in range(200):
        lib.song(b'/mnt/mmc/%03d.flac' % i)
    lib.rate(b'/mnt/mmc/005.flac', 2)
    assert lib.run()
    assert len([q for q in lib.sql if 'from songtable where' in q]) == 1


def test_an_unchanged_copy_is_not_written_again(fw, st):
    lib = Library(fw)
    lib.song(b'/mnt/mmc/a.flac')
    lib.rate(b'/mnt/mmc/a.flac', 3)
    assert lib.run()
    opened = len(fw.calls('fw_fopen'))
    fw.fn('ratings_copy_due', None)()
    assert lib.run() and not [c for c in fw.calls('fw_fopen')[opened:] if C.string_at(c[1][1]) != b'rb']


def test_a_shorter_copy_keeps_the_longer_one_as_dot_1(fw, st):
    longer = RC_HEADER + b'song\t1\t/a\t\nsong\t2\t/b\t\n'
    lib = Library(fw, {RC_FILE: longer})
    lib.song(b'/mnt/mmc/a.flac')
    lib.rate(b'/mnt/mmc/a.flac', 3)
    assert lib.run()
    assert lib.card.files[RC_FILE + '.1'] == longer and lib.copy() == RC_HEADER + b'song\t3\t/mnt/mmc/a.flac\t\n'


def test_a_dot_1_with_more_lines_still_is_kept(fw, st):
    longest, longer = RC_HEADER + b'x\n' * 9, RC_HEADER + b'x\n' * 3
    lib = Library(fw, {RC_FILE: longer, RC_FILE + '.1': longest})
    lib.song(b'/mnt/mmc/a.flac')
    lib.rate(b'/mnt/mmc/a.flac', 3)
    assert lib.run() and lib.card.files[RC_FILE + '.1'] == longest
    assert lib.copy() == RC_HEADER + b'song\t3\t/mnt/mmc/a.flac\t\n'


def test_a_longer_copy_replaces_without_a_dot_1(fw, st):
    lib = Library(fw, {RC_FILE: RC_HEADER})
    lib.song(b'/mnt/mmc/a.flac')
    lib.rate(b'/mnt/mmc/a.flac', 3)
    assert lib.run() and RC_FILE + '.1' not in lib.card.files and lib.copy().count(b'\n') == 3


def test_a_copy_that_cant_be_written_leaves_the_old_one(fw, st):
    lib = Library(fw, {RC_FILE: RC_HEADER})
    lib.song(b'/mnt/mmc/a.flac')
    lib.rate(b'/mnt/mmc/a.flac', 3)
    lib.card.fail_rename = True
    assert lib.run() and lib.copy() == RC_HEADER and RC_FILE + '.tmp' not in lib.card.files


def test_a_card_gone_by_the_end_makes_the_copy_due_again(fw, st):
    lib = Library(fw)
    lib.song(b'/mnt/mmc/a.flac')
    lib.rate(b'/mnt/mmc/a.flac', 3)
    lib.clock.now += 5000
    lib.poll()  # the pass starts
    assert lib.rc().db
    lib.card.files['/proc/mounts'] = b''  # shared over USB meanwhile
    assert lib.run() and lib.copy() is None and C.c_int.in_dll(fw.lib, 'ratings__rc_due').value == 1
    lib.card.files['/proc/mounts'] = MOUNTED
    assert lib.run(20) and lib.copy() == RC_HEADER + b'song\t3\t/mnt/mmc/a.flac\t\n'


def test_no_card_no_pass(fw, st):
    lib = Library(fw)
    lib.card.files['/proc/mounts'] = b''
    lib.rate(b'/mnt/mmc/a.flac', 3)
    assert not lib.run(12) and not fw.calls('sqlite3_open')


def test_input_holds_the_pages_off(fw, st):
    lib = Library(fw)
    lib.song(b'/mnt/mmc/a.flac')
    lib.rate(b'/mnt/mmc/a.flac', 3)
    lib.clock.now += 5000
    lib.poll()
    pages = len(lib.sql)
    for _ in range(5):
        lib.clock.now += 500
        st('last_input', C.c_uint).value = lib.clock.now - 999  # a turn under RC_QUIET_MS ago
        lib.poll()
    assert len(lib.sql) == pages and lib.rc().db


def test_a_change_waits_rc_wait_ms_and_a_run_writes_once(fw, st):
    lib = Library(fw)
    lib.song(b'/mnt/mmc/a.flac')
    lib.song(b'/mnt/mmc/b.flac')
    lib.rate(b'/mnt/mmc/a.flac', 3)
    lib.clock.now += 4999
    lib.poll()
    assert not lib.rc().db
    lib.rate(b'/mnt/mmc/b.flac', 1)  # moves the wait on
    lib.clock.now += 4999
    lib.poll()
    assert not lib.rc().db
    assert lib.run() and lib.copy().count(b'\nsong\t') == 2
    assert len([c for c in fw.calls('fw_fopen') if C.string_at(c[1][0]) == (RC_FILE + '.tmp').encode()]) == 1


def test_the_boot_pass_starts_unasked(fw, st):
    lib = Library(fw)
    lib.song(b'/mnt/mmc/f.flac', table=lib.myfav)
    lib.poll()  # the first pass marks it due
    assert lib.run() and lib.copy() == RC_HEADER + b'song\t5\t/mnt/mmc/f.flac\t\n'


def test_a_scan_ends_the_pass_and_the_new_library_starts_another(fw, st, stock_bytes):
    thread, done = stock_bytes(O['SCAN_THREAD']), stock_bytes(O['SCAN_DONE'])
    lib = Library(fw)
    lib.song(b'/mnt/mmc/a.flac')
    lib.rate(b'/mnt/mmc/a.flac', 3)
    lib.clock.now += 5000
    lib.poll()
    C.c_int.from_address(thread).value = 1  # a scan is rewriting the library
    lib.clock.now += 1000
    lib.poll()
    assert not lib.rc().db and lib.copy() is None
    for _ in range(3):
        lib.clock.now += 5000
        lib.poll()
    assert not lib.rc().db  # no pass while it runs
    C.c_int.from_address(done).value = 1
    C.c_uint.in_dll(fw.lib, 'library_gen').value += 1
    assert lib.run() and lib.copy() == RC_HEADER + b'song\t3\t/mnt/mmc/a.flac\t\n'


def test_a_failed_query_ends_the_pass_unwritten(fw, st):
    lib = Library(fw)
    lib.song(b'/mnt/mmc/a.flac')
    lib.rate(b'/mnt/mmc/a.flac', 3)
    fw.mock('sqlite3_exec', lambda db, sql, *_: 1 if b'myfav' in C.string_at(sql) else 0)
    assert lib.run() and lib.copy() is None
    assert [c[1][0] for c in fw.calls('sqlite3_close')] == [0x5150]


def test_no_database_no_pass(fw, st):
    lib = Library(fw)
    lib.rate(b'/mnt/mmc/a.flac', 3)
    fw.mock('sqlite3_open', lambda *_: 14)
    assert not lib.run(12) and lib.copy() is None


# Rockbox from Home (docs/boot.md#rockbox-from-home).
def rockbox(fw, there=True):
    def stat(version, path, info, *_):
        C.c_uint.from_address(info + 20).value = 0o100755
        return 0 if there else -1
    fw.mock('fw___xstat', stat)
    return Files(fw, {})


def test_no_rockbox_toasts_and_stays(fw, st):
    seen = toasts(fw)
    card = rockbox(fw, there=False)
    assert fw.fn('rockbox_open', C.c_int, C.c_void_p, C.c_void_p)(None, None) == 0
    assert len(seen) == 1 and not fw.calls('fw_system') and not card.files and not fw.calls('save_memoryplay_info')


def test_rockbox_leaves_its_flag_and_ends_demo(fw, power):
    card = rockbox(fw)
    power('wdt').value = 6
    fw.fn('rockbox_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert host.tools_build.ROCKBOX_FLAG in card.files  # S90play runs Rockbox on it
    cmd = C.string_at(fw.calls('fw_system')[-1][1][0])
    assert cmd.endswith(b'kill -9 $PPID') and b'checkappprocess.sh' in cmd and b'hciplayer' in cmd
    assert fw.calls('save_memoryplay_info')  # the queue kept as power-off keeps it
    assert power('ended').value == 1 and (6, WDT_STOP) in [(c[1][0], c[1][1] & 0xffffffff) for c in fw.calls('fw_ioctl')]
    assert STATE.encode() in [C.string_at(c[1][0]) for c in fw.calls('fw_unlink')]  # a clean end, nothing to report


@pytest.mark.parametrize('held, enabled', [(1, [1]), (0, [])])
def test_rockbox_gets_charging_back(fw, st, held, enabled):
    rockbox(fw)
    st('charge_held').value = held
    fw.fn('rockbox_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert [c[1][0] for c in fw.calls('switch_charge_enable')] == enabled


def bt_link(fw, link=b'1', codec=1, way=1):
    Config(fw, BTVOLLINK=link)
    fw.var('g_bluetoothflag', C.c_ubyte).value = 1
    fw.var('bt_linkstatus', C.c_ubyte).value = 1
    fw.var('fw_ctx_bt_showcoding', C.c_int).value = codec
    fw.mock('mclGetOutputWay', lambda *_: way)


@pytest.mark.parametrize('limit, level', [(100, 127), (60, 76), (150, 127), (0, 0)])
def test_rockbox_opens_the_headsets_volume_to_the_limit(fw, st, limit, level):
    rockbox(fw)
    bt_link(fw)
    fw.var('g_maxvolume', C.c_ubyte).value = limit
    fw.mock('btctl_transport_get_volume', lambda *_: 40)
    fw.fn('rockbox_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert [c[1][0] for c in fw.calls('btctl_transport_set_volume')] == [level]


@pytest.mark.parametrize('link, current', [(b'0', 40), (b'1', 128), (b'1', -1)])
def test_rockbox_leaves_a_headset_without_absolute_volume(fw, st, link, current):
    rockbox(fw)
    bt_link(fw, link)
    fw.mock('btctl_transport_get_volume', lambda *_: current)
    fw.fn('rockbox_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert not fw.calls('btctl_transport_set_volume')


# Bluetooth's absolute volume (docs/internals.md#headset-controls-and-volume).
class Btvol(C.Structure):
    _fields_ = [('checked', C.c_uint), *((k, C.c_int) for k in 'ready last failed'.split())]


class Headset:
    """AVRCP absolute volume through libbtctl: the headset's level 0-127 (past 127: none yet); a write it
    refuses returns nonzero. Stock's software gain, per channel, is what stock_btvol_trampoline gets."""
    def __init__(self, fw, level=0, refuse=False):
        self.level, self.refuse, self.gains = level, refuse, []
        fw.mock('btctl_transport_get_volume', lambda *_: self.level)
        fw.mock('btctl_transport_set_volume', self.set)
        fw.mock('stock_btvol_trampoline', lambda left, right, *_: self.gains.append((left, right)) and 0)
        self.btvol = fw.fn('ringnav_btvol', C.c_int, C.c_int, C.c_int)

    def set(self, level, *_):
        if self.refuse:
            return 1
        self.level = level
        return 0


def reads_as(level):
    """The Q2 volume (0-100) a headset level (0-127) shows as: the nearest (127 is odd, so no level is
    half way between two)."""
    return round(level * 100 / 127)


@pytest.mark.parametrize('left, right, gains', [(40, 20, (100, 50)), (20, 40, (50, 100)), (70, 70, (100, 100)), (55, 0, (100, 0))])
def test_the_headset_takes_the_volume_and_the_q2_keeps_only_the_balance(fw, left, right, gains):
    bt_link(fw)
    headset = Headset(fw)
    headset.btvol(left, right)
    assert reads_as(headset.level) == max(left, right) and headset.gains == [gains]


def test_every_volume_reads_back_as_itself(fw):
    bt_link(fw)
    headset = Headset(fw)
    for v in range(101):
        headset.btvol(v, v)
        assert reads_as(headset.level) == v
    assert len(fw.calls('btctl_transport_set_volume')) == 100  # one write per new level: 0 it showed already


def test_a_level_the_headset_already_shows_is_kept(fw):
    bt_link(fw)
    for level in range(128):
        headset = Headset(fw, level)
        headset.btvol(reads_as(level), 0)
        assert headset.level == level  # its own steps hold
    assert not fw.calls('btctl_transport_set_volume')


@pytest.mark.parametrize('kind', ['unlinked', 'no codec', 'jack', 'receiver', 'off', 'down'])
def test_without_a_ready_link_the_volume_stays_in_software(fw, kind):
    bt_link(fw, link=b'0' if kind == 'unlinked' else b'1', codec=0 if kind == 'no codec' else 1, way=0 if kind == 'jack' else 1)
    fw.var('bt__recv_pageflag', C.c_ubyte).value = kind == 'receiver'
    fw.var('g_bluetoothflag', C.c_ubyte).value = kind != 'off'
    fw.var('bt_linkstatus', C.c_ubyte).value = kind != 'down'
    headset = Headset(fw, 30)
    headset.btvol(40, 20)
    assert headset.gains == [(40, 20)] and headset.level == 30


@pytest.mark.parametrize('level, refuse', [(128, False), (200, False), (30, True)])
def test_a_headset_that_cant_or_wont_keeps_software_volume(fw, level, refuse):
    bt_link(fw)
    headset = Headset(fw, level, refuse)
    headset.btvol(40, 20)
    assert headset.gains == [(40, 20)]
    assert fw.var('navigation__btvol', Btvol).failed == 1 and fw.fn('headset_gain', C.c_int)() == fw.var('g_volume', C.c_ubyte).value


def test_zero_still_mutes_in_software(fw):
    bt_link(fw)
    headset = Headset(fw, 90)
    headset.btvol(0, 0)
    assert headset.level == 0 and headset.gains == [(0, 0)]


@pytest.mark.parametrize('volume, gain', [(35, 100), (0, 0)])
def test_headset_gain_is_full_once_the_headset_holds_the_volume(fw, volume, gain):
    bt_link(fw)
    fw.var('g_volume', C.c_ubyte).value = volume
    assert fw.fn('headset_gain', C.c_int)() == volume  # not yet
    Headset(fw).btvol(volume, volume)
    assert fw.fn('headset_gain', C.c_int)() == gain
    fw.fn('headset_volume_reset', None)()
    assert fw.fn('headset_gain', C.c_int)() == volume


class VolPoll:
    """bt_volume_poll against a ready headset at level, the clock moved by the test."""
    def __init__(self, fw, level=64, ready=True, limit=100, volume=50):
        bt_link(fw)
        self.fw, self.clock, self.headset = fw, Clock(fw), Headset(fw, level)
        fw.var('g_maxvolume', C.c_ubyte).value = limit
        fw.var('g_volume', C.c_ubyte).value = volume
        if ready:
            self.headset.btvol(volume, volume)
        self.poll = fw.fn('navigation__bt_volume_poll', None)

    def reads(self):
        return len(self.fw.calls('btctl_transport_get_volume'))


@pytest.mark.parametrize('lit, failed, limit, gap', [(1, 0, 100, 400), (1, 1, 100, O['BT_VOLUME_SLOW_MS']),
                                                     (0, 0, 80, O['BT_VOLUME_LIMIT_MS']), (0, 0, 100, O['BT_VOLUME_SLOW_MS'])])
def test_the_headsets_level_is_read_at_its_pace(fw, lit, failed, limit, gap):
    vol = VolPoll(fw, limit=limit)
    fw.var('g_backlight_status', C.c_ubyte).value = lit
    fw.var('navigation__btvol', Btvol).failed = failed
    vol.poll()
    reads = vol.reads()
    vol.clock.now += gap - 1
    vol.poll()
    assert vol.reads() == reads
    vol.clock.now += 1
    vol.poll()
    assert vol.reads() == reads + 1


@pytest.mark.parametrize('limit, want', [(100, reads_as(100)), (60, 60)])
def test_the_headsets_own_keys_move_the_q2s_volume_to_the_limit(fw, stock_bytes, limit, want):
    level = stock_bytes(0xa4cbb5, 1)  # stock responseSetVolume's level
    vol = VolPoll(fw, limit=limit, volume=40)
    vol.headset.level = 100
    vol.poll()
    assert fw.var('g_volume', C.c_ubyte).value == want and C.c_byte.from_address(level).value == want
    assert [c[1][:2] for c in fw.calls('device_set_volume')][-1] == (want, 1)
    assert [(c[1][0], C.string_at(c[1][1]), C.string_at(c[1][2])) for c in fw.calls('write_int_config')] == [(want, b'PLAYSET', b'VOLUME')]


def test_the_volume_dialogs_slider_follows_the_headset(fw, stock_bytes):
    stock_bytes(0xa4cbb5, 1)
    vol = VolPoll(fw, volume=40)
    name = C.create_string_buffer(b'volume_dialog')
    fw.mock('window_manager_get_top_window', lambda *_: 0x5100)
    fw.mock('widget_get_prop_str', lambda w, key, default, *_: C.addressof(name) if w == 0x5100 else default)
    fw.mock('widget_lookup', lambda w, key, *_: 0x5200 if C.string_at(key) == b'slider_vol' else 0)
    vol.headset.level = 127
    vol.poll()
    assert [(c[1][0], C.string_at(c[1][1]), c[1][2]) for c in fw.calls('widget_set_prop_int')] == [(0x5200, b'value', 100)]


def test_an_unmoved_headset_changes_nothing(fw):
    vol = VolPoll(fw)
    calls = len(fw.calls())
    vol.poll()
    assert [c[0] for c in fw.calls()[calls:] if c[0] in ('device_set_volume', 'write_int_config')] == []


@pytest.mark.parametrize('level', [128, -1])
def test_a_level_lost_falls_back_to_the_q2s_volume(fw, level):
    vol = VolPoll(fw, volume=33)
    vol.headset.level = level
    vol.poll()
    assert fw.var('navigation__btvol', Btvol).ready == 0 and fw.calls('device_set_volume')[-1][1][:2] == (33, 0)


def test_a_reconnect_keeps_the_q2s_volume(fw):
    vol = VolPoll(fw, ready=False, volume=27)
    vol.poll()
    assert fw.calls('device_set_volume')[-1][1][:2] == (27, 0) and not fw.calls('btctl_transport_set_volume')


def test_a_dropped_link_clears_the_state(fw):
    vol = VolPoll(fw)
    fw.var('navigation__btvol', Btvol).failed = 1
    fw.var('bt_linkstatus', C.c_ubyte).value = 0
    vol.clock.now += O['BT_VOLUME_SLOW_MS']
    vol.poll()
    state = fw.var('navigation__btvol', Btvol)
    assert (state.ready, state.failed) == (0, 0)


# The UI loop's pacing.
def test_a_dark_and_quiet_wait_stretches_to_the_dark_poll(fw):
    sleep = fw.fn('ringnav_dark_usleep', C.c_int, C.c_uint)
    for us in (10000, 999999, 1000000, 3000000):
        sleep(us)
    C.c_int.in_dll(fw.lib, 'navigation__dark_quiet').value = 1
    for us in (10000, 999999, 1000000, 3000000):
        sleep(us)
    dark = O['DARK_POLL_US']
    assert [c[1][0] for c in fw.calls('fw_usleep')] == [10000, 999999, 1000000, 3000000, dark, dark, dark, 3000000]


def test_the_cover_pollers_slow_whenever_the_screen_is_off(fw):
    sleep = fw.fn('ringnav_dark_cover_usleep', C.c_int, C.c_uint)
    sleep(50000)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    sleep(50000)
    assert [c[1][0] for c in fw.calls('fw_usleep')] == [O['DARK_POLL_US'], 50000]


class Scheduling:
    """ringnav_mainscheduling: the player's position as stock's cache has it, the clock as the test moves it."""
    def __init__(self, fw, status=2, sec=10, total=200):
        self.clock, self.status, self.sec, self.total = Clock(fw), status, sec, total
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        fw.mock('player_playtime_and_length', self.position)
        fw.mock('player_mainscheduling', lambda *_: 5)
        self.fw, self.run = fw, fw.fn('ringnav_mainscheduling', C.c_int)

    def position(self, sec, total, *_):
        C.c_int.from_address(sec).value, C.c_int.from_address(total).value = self.sec, self.total
        return 0

    def at(self, *offsets):
        """Which passes, ms after the first, ran stock's scheduling."""
        start, ran = self.clock.now, []
        for t in offsets:
            self.clock.now = start + t
            ran.append(self.run() == 5)
        return ran


def test_mainscheduling_runs_every_100_ms(fw):
    assert Scheduling(fw).at(0, 50, 99, 100, 150, 200) == [True, False, False, True, False, True]


def test_dark_and_quiet_it_runs_every_dark_poll(fw):
    sched = Scheduling(fw)
    C.c_int.in_dll(fw.lib, 'navigation__dark_quiet').value = 1
    poll = O['DARK_POLL_US'] // 1000
    assert sched.at(0, 100, poll - 1, poll) == [True, False, False, True]


def test_the_tracks_last_second_runs_every_pass_until_2_s_after(fw):
    sched = Scheduling(fw, sec=199, total=200)
    assert sched.at(0, 1, 2, 3) == [True] * 4
    sched.sec, sched.total = 0, 180  # the next track has started
    assert sched.at(0, 1, 1999, 2000, 2098, 2099) == [True, True, True, False, False, True]  # then paced again


def test_paused_at_the_end_it_stays_paced(fw):
    sched = Scheduling(fw, status=3, sec=199, total=200)
    assert sched.at(0, 1, 2) == [True, False, False]


def test_mainscheduling_at_a_zero_clock_still_paces(fw):
    sched = Scheduling(fw)
    sched.clock.now = 0
    assert sched.at(0, 50, 101) == [True, False, True]  # its stamp is 1, never 0 (a ms late)


def now_playing_top(fw, name=b'playing_page'):
    page = C.create_string_buffer(name)
    fw.mock('window_manager_get_top_window', lambda *_: 0x5100)
    fw.mock('widget_get_prop_str', lambda w, key, default, *_: C.addressof(page) if w == 0x5100 and C.string_at(key) == b'name' else default)
    return page


@pytest.mark.parametrize('top, glide, marquee, off, over, animating', [
    (b'playing_page', 1, 0, 0, 0, 1), (b'home_page', 1, 0, 0, 0, 0), (b'playing_page', 0, 0, 0, 0, 0),
    (b'playing_page', 0, 1, 5, 40, 1), (b'playing_page', 0, 1, 0, 40, 0), (b'playing_page', 0, 1, 40, 40, 0),
    (b'playing_page', 0, 0, 5, 40, 0),
])
def test_now_playing_animates_while_its_lyrics_glide_or_its_marquee_moves(fw, st, top, glide, marquee, off, over, animating):
    keep = now_playing_top(fw, top)
    st('lyric_glide').value, st('np_marquee', C.c_uint).value, st('np_off').value, st('np_over').value = glide, marquee, off, over
    assert fw.fn('navigation__np_animating', C.c_int)() == animating


class Vid(C.Structure):  # books.c's q2video player, as test_headset.py mirrors it
    _fields_ = [*((k, C.c_int) for k in 'paused volume gain pid sock seek'.split()),
                *((k, C.c_uint) for k in 'polled control_at seek_at key at paused_at'.split())]


class Wm:
    """The window manager: its vtable's paint (+0xc) at +0x94, a Python stand-in that counts."""
    PAINT = C.CFUNCTYPE(C.c_int, C.c_void_p)

    def __init__(self, fw, animating=0):
        self.block, self.vtable, self.painted = C.create_string_buffer(0x200), C.create_string_buffer(0x40), []
        self.paint = self.PAINT(lambda wm: self.painted.append(wm) or 0)
        struct.pack_into('<Q', self.vtable, 0xc, C.cast(self.paint, C.c_void_p).value)
        struct.pack_into('<Q', self.block, 0x94, C.addressof(self.vtable))
        self.p = C.addressof(self.block)
        fw.mock('window_manager_is_animating', lambda *_: animating)
        self.call = fw.fn('ringnav_wm_paint', C.c_int, C.c_void_p)


@pytest.mark.parametrize('lit, animating, painted', [(1, 0, 1), (0, 0, 0), (0, 1, 1), (1, 1, 1)])
def test_dark_the_window_manager_paints_only_an_animation(fw, lit, animating, painted):
    fw.var('g_backlight_status', C.c_ubyte).value = lit
    wm = Wm(fw, animating)
    assert wm.call(wm.p) == 0 and len(wm.painted) == painted


@pytest.mark.parametrize('away', ['video', 'game'])
def test_a_player_or_game_on_the_framebuffer_keeps_the_ui_off_it(fw, away):
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    if away == 'video':
        fw.var('books__vid', Vid).pid = 77
    else:
        fw.var('addon__gm', C.c_int).value = 78  # addon.c's gm, its pid first
    wm = Wm(fw)
    assert wm.call(wm.p) == 0 and wm.painted == []


def test_no_window_manager_is_stocks_bad_params(fw):
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    assert Wm(fw).call(None) == 0x10


# Key Tone (buzzeer_switch).
class Buzzer:
    """pthread_create runs the click's worker at once, or leaves it running, or fails."""
    def __init__(self, fw, mode='run', lit=True):
        self.mode = mode
        fw.var('g_keytone_flag', C.c_ubyte).value = 1
        fw.var('g_backlight_status', C.c_ubyte).value = lit
        fw.mock('fw_pthread_create', self.create)
        self.click = fw.fn('ringnav_buzzer', C.c_int, C.c_int)
        self.fw = fw

    def create(self, tid, attr, start, arg, *_):
        if self.mode == 'fail':
            return 11
        if self.mode == 'run':
            C.CFUNCTYPE(C.c_void_p, C.c_void_p)(start)(arg)
        return 0

    def clicks(self):
        return [c[1][0] for c in self.fw.calls('stock_buzzer_trampoline')]


def test_a_click_sounds_on_a_worker(fw):
    buzzer = Buzzer(fw)
    assert buzzer.click(1) == 0 and buzzer.click(0) == 0
    assert buzzer.clicks() == [1, 0]


def test_a_click_while_one_sounds_is_dropped(fw):
    buzzer = Buzzer(fw, 'hold')
    buzzer.click(1), buzzer.click(1)
    assert len(fw.calls('fw_pthread_create')) == 1


def test_a_worker_that_cant_start_leaves_the_next_click_free(fw):
    buzzer = Buzzer(fw, 'fail')
    buzzer.click(1)
    buzzer.mode = 'run'
    buzzer.click(1)
    assert buzzer.clicks() == [1]


def test_key_tone_off_is_silent(fw):
    buzzer = Buzzer(fw)
    fw.var('g_keytone_flag', C.c_ubyte).value = 0
    buzzer.click(1)
    assert not fw.calls('fw_pthread_create')


@pytest.mark.parametrize('listener', ['g_po_status', 'g_bal_status', 'bluetooth'])
@pytest.mark.parametrize('keytone_hp', [b'0', b'1'])
def test_headphones_silence_the_speaker_unless_click_with_headphones(fw, listener, keytone_hp):
    Config(fw, KEYTONEHP=keytone_hp)
    buzzer = Buzzer(fw)
    if listener == 'bluetooth':
        fw.mock('mclGetOutputWay', lambda *_: 1)
    else:
        fw.var(listener, C.c_ubyte).value = 1
    buzzer.click(1)
    assert buzzer.clicks() == ([1] if keytone_hp == b'1' else [])


# Widgets: a tree the C walks through stock's accessors, each widget a zeroed block holding the fields
# it reads directly (box, parent, emitter, a scroll view's flags and offsets, a selector's index).
W_X, W_PARENT, W_EMITTER = O['W_X'], O['W_PARENT'], O['W_EMITTER']
EVENT_TYPE, EVENT_KEY, EVENT_TARGET = O['EVENT_TYPE'], O['EVENT_KEY'], O['EVENT_TARGET']
SELECTOR_INDEX, SCROLL_X, SCROLL_Y = O['SELECTOR_INDEX'], O['SCROLL_X'], O['SCROLL_Y']
EVT_CLICK, EVT_DESTROY, EVT_VALUE_CHANGED = O['EVT_CLICK'], O['EVT_DESTROY'], O['EVT_VALUE_CHANGED']
CANVAS_LCD, LCD_FILL_COLOR, LCD_STROKE_COLOR = O['CANVAS_LCD'], O['LCD_FILL_COLOR'], O['LCD_STROKE_COLOR']
HANDLER = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)
TIMER = C.CFUNCTYPE(C.c_int, C.c_void_p)


class W:
    def __init__(self, ui, kind, name=None, x=0, y=0, w=375, h=40, text=None, click=False, options=0, **ints):
        self.ui, self.kind, self.name, self.options = ui, kind.encode(), name and name.encode(), options
        self.block = C.create_string_buffer(0x400)
        self.p = C.addressof(self.block)
        self.ints = {k.encode(): v for k, v in ints.items()}
        self.strs, self.children, self.parent, self.visible, self.text, self.utf8 = {}, [], None, 1, None, None
        self.opacity = 255
        struct.pack_into('<4i', self.block, W_X, x, y, w, h)
        if click:  # an emitter whose one item is an EVT_CLICK handler (clickable)
            self.item, self.emitter = C.create_string_buffer(0x40), C.create_string_buffer(0x10)
            struct.pack_into('<i', self.item, O['EMIT_TYPE'], EVT_CLICK)
            struct.pack_into('<Q', self.emitter, 0, C.addressof(self.item))
            struct.pack_into('<Q', self.block, W_EMITTER, C.addressof(self.emitter))
        if text is not None:
            self.set_text(text)
        ui.by[self.p] = self

    def add(self, child):
        child.parent = self
        self.children.append(child)
        struct.pack_into('<Q', child.block, W_PARENT, self.p)
        return child

    def set_text(self, text):
        self.text = (C.c_uint * (len(text) + 1))(*map(ord, text), 0)

    def int(self, off, value=None):
        if value is None:
            return struct.unpack_from('<i', self.block, off)[0]
        struct.pack_into('<i', self.block, off, value)

    def box(self):
        return struct.unpack_from('<4i', self.block, W_X)

    def find(self, name):
        for c in self.children:
            if c.name == name:
                return c
            got = c.find(name)
            if got:
                return got
        return None


class Ui:
    """Stock's widget and window-manager calls over W trees; timers, clicks and handlers kept to run."""
    def __init__(self, fw, lit=True):
        self.fw, self.by, self.strings, self.top = fw, {}, {}, None
        self.animating, self.timers, self.next_timer, self.clicks, self.handlers = 0, {}, 100, [], []
        self.wm = W(self, 'window_manager')
        self.invalidated = []
        m = fw.mock
        m('window_manager', lambda *_: self.wm.p)
        m('window_manager_get_top_window', lambda *_: self.top.p if self.top else 0)
        m('window_manager_is_animating', lambda *_: self.animating)
        m('window_manager_get_pointer_pressed', lambda *_: 0)
        m('widget_get_type', lambda w, *_: self.cstr(self.by[w].kind) if w in self.by else self.cstr(b''))
        m('widget_get_prop_str', self.get_str)
        m('widget_get_prop_int', lambda w, key, default, *_: self.by[w].ints.get(C.string_at(key), C.c_int(default).value)
          if w in self.by else default)
        m('widget_get_prop_bool', lambda w, key, default, *_: self.by[w].ints.get(C.string_at(key), default) if w in self.by else default)
        m('widget_set_prop_int', self.set_int)
        m('widget_set_prop_str', lambda w, key, value, *_: self.by[w].strs.__setitem__(C.string_at(key), C.string_at(value)) and 0)
        m('widget_get_visible', lambda w, *_: self.by[w].visible if w in self.by else 0)
        m('widget_set_visible', lambda w, v, *_: setattr(self.by[w], 'visible', v) if w in self.by else 0)
        m('widget_count_children', lambda w, *_: len(self.by[w].children) if w in self.by else 0)
        m('widget_get_child', lambda w, i, *_: self.by[w].children[i].p if w in self.by and i < len(self.by[w].children) else 0)
        m('widget_lookup', self.lookup)
        m('widget_get_text', lambda w, *_: C.addressof(self.by[w].text) if w in self.by and self.by[w].text else 0)
        m('widget_set_text_utf8', lambda w, s, *_: setattr(self.by[w], 'utf8', C.string_at(s)) if w in self.by else 0)
        m('widget_set_opacity', lambda w, v, *_: setattr(self.by[w], 'opacity', v) if w in self.by else 0)
        m('widget_move_resize', lambda w, x, y, ww, hh, *_: struct.pack_into('<4i', self.by[w].block, W_X, x, y, ww, hh) if w in self.by else 0)
        m('widget_invalidate_force', lambda w, *_: self.invalidated.append(w))
        m('widget_on', self.on)
        m('image_create', lambda parent, x, y, w, h, *_: self.by[parent].add(W(self, 'image', x=x, y=y, w=w, h=h)).p)
        m('timer_add', self.timer_add)
        m('timer_remove', lambda t, *_: self.timers.pop(t, None) and 0)
        m('stock_dispatch_trampoline', self.dispatch)
        m('pointer_event_init', self.pointer_event)
        m('text_selector_count_options', lambda w, *_: self.by[w].options)
        m('text_selector_set_selected_index', lambda w, i, *_: self.by[w].int(SELECTOR_INDEX, i))
        m('scroll_view_set_offset', lambda w, x, y, *_: (self.by[w].int(SCROLL_X, x), self.by[w].int(SCROLL_Y, y)) and 0)
        m('stock_keyup_trampoline', lambda *_: 0)
        fw.var('g_backlight_status', C.c_ubyte).value = lit

    def cstr(self, s):
        if s not in self.strings:
            self.strings[s] = C.create_string_buffer(s)
        return C.addressof(self.strings[s])

    def get_str(self, w, key, default, *_):
        node, key = self.by.get(w), C.string_at(key)
        if node and key == b'name' and node.name:
            return self.cstr(node.name)
        if node and key in node.strs:
            return self.cstr(node.strs[key])
        return default

    def set_int(self, w, key, value, *_):
        if w in self.by:
            self.by[w].ints[C.string_at(key)] = C.c_int(value).value
        return 0

    def lookup(self, w, name, *_):
        node = self.by.get(w)
        found = node.find(C.string_at(name)) if node else None
        return found.p if found else 0

    def on(self, w, kind, fn, ctx, *_):
        self.handlers.append((w, kind, fn, ctx))
        return len(self.handlers)

    def emit(self, w, kind):
        """Run w's handlers for kind, as stock's emitter would."""
        e = C.create_string_buffer(0x40)
        struct.pack_into('<i', e, EVENT_TYPE, kind)
        for hw, hk, fn, ctx in list(self.handlers):
            if hw == w.p and hk == kind:
                HANDLER(fn)(ctx, C.addressof(e))

    def timer_add(self, fn, ctx, ms, *_):
        self.next_timer += 1
        self.timers[self.next_timer] = (fn, ctx, ms)
        return self.next_timer

    def fire(self, fn_name=None, timer=None):
        """Run a timer, the one whose function is fn_name (a static's navigation__ name) if given; a
        result other than 8 (RET_REPEAT) removes it, as stock does."""
        if timer is None:
            want = C.cast(getattr(self.fw.lib, fn_name), C.c_void_p).value
            timer = [t for t, (fn, _, _) in self.timers.items() if fn == want][-1]
        fn, ctx, _ = self.timers[timer]
        result = TIMER(fn)(ctx)
        if result != 8:
            self.timers.pop(timer, None)
        return result

    def due(self, fn_name):
        want = C.cast(getattr(self.fw.lib, fn_name), C.c_void_p).value
        return [t for t, (fn, _, _) in self.timers.items() if fn == want]

    def dispatch(self, target, event, *_):
        node = self.by.get(target)
        self.clicks.append((node.name if node else target, C.c_int.from_address(event + EVENT_TYPE).value if event else None))
        return 0

    def pointer_event(self, buf, kind, target, x, y, *_):
        C.c_int.from_address(buf + EVENT_TYPE).value = kind
        C.c_void_p.from_address(buf + EVENT_TARGET).value = target
        return buf

    def window(self, name, kind='window'):
        top = W(self, kind, name, h=320)
        struct.pack_into('<Q', top.block, W_PARENT, self.wm.p)
        self.top = top
        return top


def key_event(key, kind=UP):
    e = C.create_string_buffer(0x40)
    struct.pack_into('<i', e, EVENT_TYPE, kind)
    struct.pack_into('<i', e, EVENT_KEY, key)
    return e


def list_page(ui, name='sysset_page', rows=5, row_h=40, height=200):
    """A settings page as stock builds one: a list_view holding a vertical scroll_view of clickable rows
    (and its scroll bar)."""
    top = ui.window(name)
    lv = top.add(W(ui, 'list_view', h=height))
    sv = lv.add(W(ui, 'scroll_view', 'view', h=height))
    sv.block[O['VIEW_VERTICAL']] = 1
    sv.int(O['VIEW_CONTENT_H'], rows * row_h)
    for i in range(rows):
        sv.add(W(ui, 'list_item', 'row%d' % i, y=i * row_h, h=row_h, text='Row %d' % i, click=True))
    lv.add(W(ui, 'scroll_bar_m'))
    return sv


def select(fw, sv, row):
    """The list as its page left it, row selected and in view (ringnav_select, a page's own row)."""
    fw.fn('ringnav_select', None, C.c_void_p, C.c_int, C.c_int)(sv.p, row, len(sv.children))
    assert sv.ints[SEL] == row


SEL = b'_ringnav_index'


class Keys:
    """ringnav, the key release filter, with the clock the test moves."""
    def __init__(self, fw, stock_bytes, now=100000):
        self.clock = Clock(fw, now)
        for at in (O['KEY_LOCKOUT'], O['BOOT_KEY_GUARD'], O['RETURN_RELEASE_LATCH']):
            stock_bytes(at, 1)
        stock_bytes(O['SCAN_DONE'])
        self.ringnav = fw.fn('ringnav', C.c_int, C.c_void_p, C.c_void_p)
        self.keydown = fw.fn('ringnav_keydown', C.c_int, C.c_void_p, C.c_void_p)

    def __call__(self, key, after=0):
        self.clock.now += after
        return self.ringnav(None, key_event(key))

    def press(self, key, after=0):
        self.clock.now += after
        return self.keydown(None, key_event(key, DOWN))


@pytest.fixture
def keys(fw, stock_bytes):
    return Keys(fw, stock_bytes)


def test_the_wheel_moves_the_selection_a_row_a_tick(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui)
    select(fw, sv, 0)  # the page as its first paint left it
    assert keys(KEY_NEXT) == STOP and sv.ints[SEL] == 1
    assert keys(KEY_NEXT, 200) == STOP and sv.ints[SEL] == 2
    assert keys(KEY_PREV, 200) == STOP and sv.ints[SEL] == 1


def test_a_ring_list_bumps_at_its_end_then_carries_over_after_a_pause(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui)
    select(fw, sv, 4)
    keys(KEY_NEXT)
    assert sv.ints[SEL] == 4 and ui.due('navigation__fx_expire')  # the bump repaints
    keys(KEY_NEXT, 100)
    assert sv.ints[SEL] == 4  # a spin hard-stops at the end
    keys(KEY_NEXT, O['EDGE_PAUSE_MS'])
    assert sv.ints[SEL] == 0
    keys(KEY_PREV, 50)
    keys(KEY_PREV, O['EDGE_PAUSE_MS'])
    assert sv.ints[SEL] == 4  # and back round the top


def test_a_list_that_isnt_a_ring_stops_at_its_end(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui, 'about_page')
    select(fw, sv, 4)
    for _ in range(3):
        keys(KEY_NEXT, 1000)
    assert sv.ints[SEL] == 4


TICK_MS = 40  # a brisk spin's ticks


def test_a_fast_spin_on_a_long_list_speeds_up(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui, rows=SHORT_LIST_MAX + 200)
    select(fw, sv, 0)
    seen = []
    for i in range(30):
        keys(KEY_NEXT, 0 if i == 0 else TICK_MS)
        seen.append(sv.ints[SEL])
    steps = [b - a for a, b in zip([0] + seen, seen)]
    first = len(range(0, LIST_FIRST_MS, TICK_MS))  # the ticks spun inside LIST_FIRST_MS
    assert steps[:first] == [1] * first and steps[first] > 1 and steps == sorted(steps)  # one row a tick, then faster
    assert ui.due('navigation__letter_expire')  # the fast-scroll letter shows


def test_a_short_list_never_speeds_up(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui, rows=SHORT_LIST_MAX)
    select(fw, sv, 0)
    assert (SHORT_LIST_MAX - 2) * TICK_MS > LIST_FIRST_MS  # long enough a spin to speed a long list up
    seen = []
    for _ in range(SHORT_LIST_MAX - 1):
        keys(KEY_NEXT, TICK_MS)
        seen.append(sv.ints[SEL])
    assert seen == list(range(1, SHORT_LIST_MAX))  # a row a tick to the last


def test_the_wheel_scrolls_the_selected_row_into_view(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui, rows=20, height=200)
    select(fw, sv, 0)
    for _ in range(10):
        keys(KEY_NEXT, 200)
    top = sv.int(SCROLL_Y)
    assert sv.ints[SEL] == 10 and top <= 10 * 40 and 11 * 40 <= top + 200  # row 10 shows whole


def test_centre_clicks_the_row_once_its_double_press_window_passes(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui)
    select(fw, sv, 2)
    assert keys(KEY_CENTER) == STOP and ui.clicks == []
    assert [ui.timers[t][2] for t in ui.due('navigation__confirm_center')] == [O['DOUBLE_CLICK_MS']]
    keys.clock.now += O['DOUBLE_CLICK_MS']
    ui.fire('navigation__confirm_center')
    assert ui.clicks == [(b'row2', EVT_CLICK)]


def test_a_double_centre_is_stocks_screen_off_and_clicks_nothing(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui)
    select(fw, sv, 2)
    keys(KEY_CENTER)
    assert keys(KEY_CENTER, 100) == 0  # stock's downstream short press turns the screen off
    assert not ui.due('navigation__confirm_center') and ui.clicks == []


def test_a_row_changed_under_a_pending_centre_clicks_nothing(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui)
    select(fw, sv, 2)
    keys(KEY_CENTER)
    timer = ui.due('navigation__confirm_center')[0]
    sv.children[2].set_text('Another row')  # the list rebound under it
    keys.clock.now += O['DOUBLE_CLICK_MS']
    ui.fire(timer=timer)
    assert ui.clicks == []


def test_a_wheel_tick_ends_a_pending_centre(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui)
    select(fw, sv, 2)
    keys(KEY_CENTER)
    keys(KEY_NEXT, 50)
    assert not ui.due('navigation__confirm_center') and sv.ints[SEL] == 3


def test_a_page_outside_the_allowlist_keeps_stocks_keys(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui, 'unknown_page')
    select(fw, sv, 0)
    assert keys(KEY_NEXT) == 0 and sv.ints[SEL] == 0


def test_a_tick_while_a_page_slides_is_held_and_replayed(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui)
    select(fw, sv, 0)
    ui.animating = 1
    assert keys(KEY_NEXT) == STOP and keys(KEY_NEXT, 10) == STOP and sv.ints[SEL] == 0
    timers = ui.due('navigation__replay_wheel')
    assert len(timers) == 1 and ui.timers[timers[0]][2] == O['HOLD_POLL_MS']
    assert ui.fire(timer=timers[0]) == 8 and sv.ints[SEL] == 0  # still sliding: it waits
    ui.animating = 0
    keys.clock.now += 50
    assert ui.fire(timer=timers[0]) == 8 and sv.ints[SEL] == 1
    assert ui.fire(timer=timers[0]) == 0 and sv.ints[SEL] == 2 and not ui.due('navigation__replay_wheel')


def test_a_reversal_drops_the_held_ticks(fw, keys, st):
    ui = Ui(fw)
    sv = list_page(ui, rows=40)
    select(fw, sv, 2)
    ui.animating = 1
    for _ in range(O['HOLD_MAX']):
        keys(KEY_NEXT, 5)
    keys(KEY_PREV, 5)  # one tick back drops them all, not one of them, and is dropped itself
    assert st('held').value == 0
    ui.animating = 0
    keys.clock.now += 50
    while ui.due('navigation__replay_wheel'):
        ui.fire('navigation__replay_wheel')
    assert sv.ints[SEL] == 2


def test_a_reversal_after_the_slide_drops_the_ticks_still_replaying(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui, rows=40)
    select(fw, sv, 5)
    ui.animating = 1
    for _ in range(3):
        keys(KEY_NEXT, 5)
    ui.animating = 0
    keys(KEY_PREV, 5)  # the page is ready, its replay not yet run: the tick back drops the turn
    keys.clock.now += 50
    while ui.due('navigation__replay_wheel'):
        ui.fire('navigation__replay_wheel')
    assert sv.ints[SEL] == 5


def test_hold_max_ticks_all_play_into_a_list_that_loads_late(fw, keys, st):
    ui = Ui(fw)
    ui.window('sysset_page')  # its list not built yet
    for _ in range(O['HOLD_MAX']):
        keys(KEY_NEXT, 5)
    ui.fire('navigation__replay_wheel')  # a poll before the list is built holds its tick again
    assert st('held').value == O['HOLD_MAX']
    sv = list_page(ui, rows=40)
    select(fw, sv, 0)
    while ui.due('navigation__replay_wheel'):
        ui.fire('navigation__replay_wheel')
    assert sv.ints[SEL] == O['HOLD_MAX']


def test_held_ticks_too_old_are_dropped(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui)
    select(fw, sv, 0)
    ui.animating = 1
    keys(KEY_NEXT)
    ui.animating = 0
    keys.clock.now += O['HOLD_MS'] + 1
    assert ui.fire('navigation__replay_wheel') == 0 and sv.ints[SEL] == 0


def test_a_tick_before_the_list_loads_is_held_for_it(fw, keys):
    ui = Ui(fw)
    ui.window('sysset_page')  # its list not built yet
    keys(KEY_NEXT)
    assert ui.due('navigation__replay_wheel')
    sv = list_page(ui)  # the page as it has loaded
    select(fw, sv, 0)
    ui.fire('navigation__replay_wheel')
    assert sv.ints[SEL] == 1


def test_held_ticks_stop_at_hold_max(fw, keys, st):
    ui = Ui(fw)
    list_page(ui, rows=40)
    ui.animating = 1
    for _ in range(20):
        keys(KEY_NEXT, 5)
    assert st('held').value == O['HOLD_MAX']


def test_a_button_ends_a_held_turn(fw, keys, st):
    ui = Ui(fw)
    list_page(ui)
    ui.animating = 1
    keys(KEY_NEXT)
    keys(KEY_RETURN, 5)
    assert st('held').value == 0


def test_key_tone_clicks_once_per_row_change_on_a_list(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui, 'about_page')
    select(fw, sv, 3)
    tone = fw.var('g_keytone_flag', C.c_ubyte)
    tone.value = 1
    seen = []
    fw.mock('stock_keydown_trampoline', lambda *_: seen.append(tone.value) and 0)
    keys.press(KEY_NEXT)
    assert seen == [0] and tone.value == 1  # the press is silenced, stock's own handling still runs
    keys(KEY_NEXT)
    assert sv.ints[SEL] == 4 and len(fw.calls('buzzeer_switch')) == 1
    keys.press(KEY_NEXT, 300)
    keys(KEY_NEXT)
    assert sv.ints[SEL] == 4 and len(fw.calls('buzzeer_switch')) == 1  # no row change, no click


@pytest.mark.parametrize('key', [KEY_PLAY, KEY_CENTER, KEY_FWD_BTN])
def test_other_keys_keep_stocks_press_click(fw, keys, key):
    ui = Ui(fw)
    list_page(ui)
    tone = fw.var('g_keytone_flag', C.c_ubyte)
    tone.value = 1
    seen = []
    fw.mock('stock_keydown_trampoline', lambda *_: seen.append(tone.value) and 0)
    keys.press(key)
    assert seen == [1]


def test_a_press_on_a_page_without_rows_keeps_its_click(fw, keys):
    ui = Ui(fw)
    ui.window('sysset_page')
    tone = fw.var('g_keytone_flag', C.c_ubyte)
    tone.value = 1
    seen = []
    fw.mock('stock_keydown_trampoline', lambda *_: seen.append(tone.value) and 0)
    keys.press(KEY_NEXT)
    assert seen == [1]


@pytest.mark.parametrize('name, stock, result', [(b'coverflow_page', 0, STOP), (b'coverflow_page', 5, 5), (b'home_page', 0, 0)])
def test_coverflows_wheel_press_stops_short_of_the_list_under_it(fw, keys, name, stock, result):
    ui = Ui(fw)
    ui.window(name.decode())
    fw.mock('stock_keydown_trampoline', lambda *_: stock)
    assert keys.press(KEY_NEXT) == result


def test_no_event_drops_the_pending_press(fw, keys, st):
    ui = Ui(fw)
    sv = list_page(ui)
    select(fw, sv, 1)
    keys(KEY_CENTER)
    assert fw.fn('ringnav', C.c_int, C.c_void_p, C.c_void_p)(None, None) == 0
    assert not ui.due('navigation__confirm_center') and st('center_timer', C.c_uint).value == 0


@pytest.mark.parametrize('key', [KEY_CENTER, KEY_RETURN])
def test_centre_or_return_closes_quick_settings(fw, keys, key):
    ui = Ui(fw)
    top = ui.window('statusbar_dialog', 'dialog')
    assert keys(key) == STOP
    assert [c[1][0] for c in fw.calls('dialog_quit')] == [top.p]


def test_a_held_returns_release_goes_to_stock(fw, keys):
    ui = Ui(fw)
    ui.window('statusbar_dialog', 'dialog')
    C.c_ubyte.from_address(O['RETURN_RELEASE_LATCH']).value = 1
    keys(KEY_RETURN)
    assert not fw.calls('dialog_quit') and fw.calls('stock_keyup_trampoline')


@pytest.mark.parametrize('done, closed', [(1, 1), (0, 0)])
def test_a_finished_scans_dialog_closes_at_once(fw, keys, done, closed):
    ui = Ui(fw)
    ui.window('updatemusic_dialog', 'dialog')
    C.c_int.from_address(O['SCAN_DONE']).value = done
    keys(KEY_RETURN)
    assert len(fw.calls('dialog_quit')) == closed


def search_edit(fw, ui, name):
    im, edit = C.create_string_buffer(0x40), W(ui, 'edit', name)
    struct.pack_into('<Q', im, O['IM_WIDGET'], edit.p)
    fw.mock('input_method', lambda *_: C.addressof(im))
    return im, edit


EDIT_NAME = host.tools_build.need(re.search(r'#define EDIT_NAME "([^"]+)"', (host.ROOT/'patch/peq.h').read_text()),
                                  'no EDIT_NAME in peq.h')[1]  # our own T9 edits


@pytest.mark.parametrize('name, emptied', [('search_edit', False), (EDIT_NAME, True)])
def test_return_closes_the_keyboard_before_the_page(fw, keys, name, emptied):
    ui = Ui(fw)
    ui.window('search_dialog', 'dialog')
    im, edit = search_edit(fw, ui, name)
    edit.utf8 = b'abc'
    assert keys(KEY_RETURN) == STOP
    assert [c[1][:2] for c in fw.calls('widget_set_focused')] == [(edit.p, 0)]
    assert [c[1][:2] for c in fw.calls('input_method_request')] == [(C.addressof(im), 0)]
    assert (edit.utf8 == b'') == emptied and not fw.calls('stock_keyup_trampoline')  # ours is a cancel


def test_return_on_another_edit_goes_to_stock(fw, keys):
    ui = Ui(fw)
    ui.window('search_dialog', 'dialog')
    search_edit(fw, ui, 'other_edit')
    keys(KEY_RETURN)
    assert not fw.calls('input_method_request') and fw.calls('stock_keyup_trampoline')


def keyboard_up(fw, ui):
    """Search with T9 up: the keyboard is the top window, the history list under it (#365)."""
    sv = list_page(ui, 'search_dialog')
    search_edit(fw, ui, 'search_edit')
    ui.window('kb_default_t9', 'keyboard')
    return sv


def screen_offs(fw):
    return [C.c_int.from_address(c[1][1] + EVENT_KEY).value for c in fw.calls('on_wm_keyup_fun')]


@pytest.mark.parametrize('gap', [None, O['DOUBLE_CLICK_MS']])
def test_one_centre_over_the_keyboard_does_nothing(fw, keys, gap):
    """Stock's power key would turn the screen off on it; a second after the double press's window is one too."""
    ui = Ui(fw)
    keyboard_up(fw, ui)
    presses = [keys(KEY_CENTER)] + ([keys(KEY_CENTER, gap)] if gap else [])
    assert presses == [STOP] * len(presses)  # stock's on_wm_keyup_fun never sees it
    assert not screen_offs(fw) and not ui.clicks and not ui.timers and not fw.calls('input_method_request')


def test_a_double_centre_over_the_keyboard_turns_the_screen_off_once(fw, keys):
    ui = Ui(fw)
    keyboard_up(fw, ui)
    keys(KEY_CENTER)
    assert keys(KEY_CENTER, O['DOUBLE_CLICK_MS'] - 1) == STOP and screen_offs(fw) == [KEY_CENTER]
    assert keys(KEY_CENTER, 1) == STOP and screen_offs(fw) == [KEY_CENTER]  # a third starts a new pair
    assert not ui.clicks and not ui.timers


def test_the_wheel_over_the_keyboard_and_centre_over_the_volume_stay_stocks(fw, keys):
    ui = Ui(fw)
    keyboard_up(fw, ui)
    assert keys(KEY_NEXT) == 0  # the volume
    ui.window('volume_dialog', 'dialog')
    assert keys(KEY_CENTER, 1000) == 0  # stock's screen off


def test_a_key_stock_takes_is_stocks_and_ends_the_run(fw, keys, st):
    ui = Ui(fw)
    sv = list_page(ui, rows=SHORT_LIST_MAX + 10)  # long enough to time a run
    select(fw, sv, 1)
    keys(KEY_NEXT)
    assert sv.ints[SEL] == 2 and st('wheel_run', C.c_uint).value  # a spin under way
    fw.mock('stock_keyup_trampoline', lambda *_: 5)
    assert keys(KEY_NEXT, TICK_MS) == 5 and sv.ints[SEL] == 2 and st('wheel_run', C.c_uint).value == 0


def test_a_long_power_press_release_clicks_nothing(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui)
    select(fw, sv, 1)
    fw.var('g_power_longkey', C.c_ubyte).value = 1
    assert keys(KEY_CENTER) == 0 and not ui.due('navigation__confirm_center')


def test_a_centre_with_no_timer_to_spare_clicks_nothing(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui)
    select(fw, sv, 1)
    fw.mock('timer_add', lambda *_: 0)
    assert keys(KEY_CENTER) == STOP and ui.clicks == []


def test_an_overdue_centre_clicks_before_the_next_key(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui)
    select(fw, sv, 1)
    keys(KEY_CENTER)
    keys(KEY_CENTER, O['DOUBLE_CLICK_MS'] + 50)  # its timer never ran (a stalled loop)
    assert ui.clicks[:1] == [(b'row1', EVT_CLICK)]


def test_a_double_centre_off_a_list_turns_the_screen_off(fw, keys):
    ui = Ui(fw)
    ui.window('about_page')  # no rows
    keys(KEY_CENTER)
    assert keys(KEY_CENTER, 100) == STOP
    assert [C.c_int.from_address(c[1][1] + EVENT_KEY).value for c in fw.calls('on_wm_keyup_fun')] == [KEY_CENTER]


def test_a_touch_scroll_under_the_wheel_is_stopped_and_forgotten(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui)
    select(fw, sv, 1)
    sv.ints[b'_ringnav_touch'] = 1
    keys(KEY_NEXT)
    assert sv.ints[b'_ringnav_touch'] == 0 and sv.ints[SEL] == 2


def test_a_page_without_rows_scrolls_by_the_ring_step(fw, keys):
    ui = Ui(fw)
    top = ui.window('about_page')
    sv = top.add(W(ui, 'scroll_view', 'text', h=200))
    sv.block[O['VIEW_VERTICAL']] = 1
    sv.int(O['VIEW_CONTENT_H'], 1000)
    sv.add(W(ui, 'label', h=1000, text='A long text'))
    step = host.tools_build.RING_STEP
    keys(KEY_NEXT)
    keys(KEY_NEXT, 200)
    assert sv.int(SCROLL_Y) == 2 * step
    for _ in range(30):
        keys(KEY_NEXT, 200)
    assert sv.int(SCROLL_Y) == 1000 - 200  # the view's own end
    keys(KEY_PREV, 200)
    assert sv.int(SCROLL_Y) == 800 - step


def test_return_on_now_playing_leaves_full_screen_lyrics_first(fw, keys, st):
    ui = Ui(fw)
    top = ui.window('playing_page')
    column = top.add(W(ui, 'view', 'column'))
    st('np_win', C.c_void_p).value = top.p
    st('np_slide', C.c_void_p).value = column.add(W(ui, 'slide_view', 'slide_view')).p
    st('np_lrc', C.c_void_p).value = column.add(W(ui, 'scroll_view', 'lyrics')).p
    st('np_full').value = 1
    assert keys(KEY_RETURN) == STOP and st('np_full').value == 0  # back to the column, still on the page


def test_return_by_button_ends_touch_mode_after_stocks_slide(fw, keys, st):
    ui = Ui(fw)
    list_page(ui)
    st('touch_mode').value = 1
    keys(KEY_RETURN)
    assert ui.due('navigation__untouch') and st('touch_mode').value == 1
    fw.mock('timer_add', lambda *_: 0)
    keys(KEY_RETURN, 10)
    assert st('touch_mode').value == 0  # no timer: at once


# The setting editors (stock's pages with no list).
def slider_window(ui, name='backlight_page'):
    top = ui.window(name)
    top.add(W(ui, 'button', 'img_dec', click=True))
    top.add(W(ui, 'button', 'img_add', click=True))
    return top


@pytest.mark.parametrize('name', ['backlight_page', 'maxvol_page', 'bootvol_page', 'balance_page'])
def test_the_wheel_steps_a_setting_through_its_own_buttons(fw, keys, name):
    ui = Ui(fw)
    slider_window(ui, name)
    assert keys(KEY_NEXT) == STOP and keys(KEY_PREV, 10) == STOP and keys(KEY_NEXT, 10) == STOP
    assert ui.clicks == [(b'img_add', EVT_CLICK), (b'img_dec', EVT_CLICK), (b'img_add', EVT_CLICK)]


def test_a_disabled_button_is_not_pressed(fw, keys):
    ui = Ui(fw)
    top = slider_window(ui)
    top.find(b'img_add').ints[b'enable'] = 0  # at the maximum
    keys(KEY_NEXT)
    assert ui.clicks == []


def test_the_wheel_waits_for_a_sliding_page(fw, keys):
    ui = Ui(fw)
    slider_window(ui)
    ui.animating = 1
    assert keys(KEY_NEXT) == STOP and ui.clicks == []


def test_centre_goes_back_from_a_setting_once_no_second_press_comes(fw, keys, stock_bytes):
    ui = Ui(fw)
    slider_window(ui)
    assert keys(KEY_CENTER) == STOP and not fw.calls('navigator_back')
    C.c_ubyte.from_address(O['KEY_LOCKOUT']).value = 8  # stock's lockout after the centre key
    ui.fire('navigation__edit_single')
    assert len(fw.calls('navigator_back')) == 1 and C.c_ubyte.from_address(O['KEY_LOCKOUT']).value == 0


def test_a_double_centre_on_a_setting_turns_the_screen_off(fw, keys):
    ui = Ui(fw)
    slider_window(ui)
    keys(KEY_CENTER)
    keys(KEY_CENTER, 100)
    assert [C.c_int.from_address(c[1][1] + EVENT_KEY).value for c in fw.calls('on_wm_keyup_fun')] == [KEY_CENTER]
    assert not ui.due('navigation__edit_single') and not fw.calls('navigator_back')


def test_a_centre_after_an_overdue_one_runs_it_and_waits_again(fw, keys):
    ui = Ui(fw)
    slider_window(ui)
    keys(KEY_CENTER)
    keys(KEY_CENTER, 400)  # the first's timer never ran
    assert len(fw.calls('navigator_back')) == 1 and len(ui.due('navigation__edit_single')) == 1


def test_a_pending_centre_does_nothing_while_the_page_slides(fw, keys):
    ui = Ui(fw)
    slider_window(ui)
    keys(KEY_CENTER)
    ui.animating = 1
    ui.fire('navigation__edit_single')
    assert not fw.calls('navigator_back')


def quick_window(ui, value=50, lo=10, hi=100, visible=1):
    top = ui.window('statusbar_dialog', 'dialog')
    slider = top.add(W(ui, 'slider', 'slider_backlight', value=value, min=lo, max=hi))
    slider.visible = visible
    return slider


@pytest.mark.parametrize('value, key, want', [(50, KEY_NEXT, 51), (50, KEY_PREV, 49), (100, KEY_NEXT, 100), (10, KEY_PREV, 10)])
def test_quick_settings_wheel_sets_the_brightness_slider(fw, keys, value, key, want):
    ui = Ui(fw)
    I = fw.var('fw_ctx_g_dropdown_menu_state', C.c_int)
    I.value = 3  # open
    slider = quick_window(ui, value)
    assert keys(key) == STOP and slider.ints[b'value'] == want


@pytest.mark.parametrize('state, timer, visible', [(1, -1, 1), (2, -1, 1), (3, 4, 1), (3, -1, 0)])
def test_quick_settings_wheel_waits_while_it_slides(fw, keys, state, timer, visible):
    ui = Ui(fw)
    fw.var('fw_ctx_g_dropdown_menu_state', C.c_int).value = state
    slider = quick_window(ui, visible=visible)
    slider.parent.ints[b'timer_idex'] = timer
    assert keys(KEY_NEXT) == STOP and slider.ints[b'value'] == 50


def test_quick_settings_wheel_as_volume_is_stocks(fw, keys):
    Config(fw, QSVOLUME=b'1')
    ui = Ui(fw)
    fw.var('fw_ctx_g_dropdown_menu_state', C.c_int).value = 3
    slider = quick_window(ui)
    keys(KEY_NEXT)
    assert slider.ints[b'value'] == 50 and fw.calls('stock_keyup_trampoline')


def time_window(ui, name='manualtime_page', at=(4, 2, 4, 9, 5)):
    """Stock's Set Date and Time page: a slide view of a date page (year from 2022, month, day) and a time
    page (hour 0-23, minute), and OK; the sleep timer's has hour and minute only."""
    top = ui.window(name)
    slide = top.add(W(ui, 'slide_view', 'slide_view', value=0))
    pages = [slide.add(W(ui, 'view', 'date')), slide.add(W(ui, 'view', 'time'))]
    for page in pages:
        page.add(W(ui, 'view', 'view_back'))
    fields = [('tselector_year', 30, 0), ('tselector_month', 12, 0), ('tselector_day', 31, 0), ('tselector_hour', 24, 1), ('tselector_min', 60, 1)]
    for (field, options, page), index in zip(fields, at):
        if name == 'manualtime_page' or page:
            pages[page].add(W(ui, 'text_selector', field, options=options)).int(SELECTOR_INDEX, index)
    top.add(W(ui, 'view', 'img_dot'))
    top.add(W(ui, 'view', 'slide_indicator'))
    top.add(W(ui, 'view', 'ok_box')).add(W(ui, 'button', 'btn_enter', click=True, text='OK'))
    return top


def field(top):
    return top.ints.get(b'_edit_field', 0)


def index(top, name):
    return top.find(name.encode()).int(SELECTOR_INDEX)


def centre(ui, keys):
    """A single Centre, its double-press window gone."""
    keys(KEY_CENTER, 300)
    ui.fire('navigation__edit_single')


def test_the_wheel_turns_the_edited_field_within_its_options(fw, keys):
    ui = Ui(fw)
    top = time_window(ui)
    top.ints[b'_edit_field'] = 1
    keys(KEY_NEXT)
    assert index(top, 'tselector_month') == 3
    for _ in range(20):
        keys(KEY_NEXT, 10)
    assert index(top, 'tselector_month') == 11 and field(top) == 1
    assert index(top, 'tselector_year') == 4  # the others stay


def test_centre_walks_the_fields_then_presses_ok(fw, keys):
    ui = Ui(fw)
    top = time_window(ui)
    seen = []
    for _ in range(5):
        centre(ui, keys)
        seen.append(field(top))
    assert seen == [1, 2, 3, 4, 5] and ui.clicks == []
    assert top.find(b'slide_view').ints[b'value'] == 1  # the time page shows its fields
    centre(ui, keys)
    assert ui.clicks == [(b'btn_enter', EVT_CLICK)] and len(fw.calls('navigator_back')) == 1  # set, and back


@pytest.mark.parametrize('key, want', [(KEY_NEXT, 0), (KEY_PREV, 4)])
def test_the_wheel_on_ok_goes_back_to_the_fields(fw, keys, key, want):
    ui = Ui(fw)
    top = time_window(ui)
    top.ints[b'_edit_field'] = 5
    top.find(b'slide_view').ints[b'value'] = 1
    keys(key)
    assert field(top) == want and top.find(b'slide_view').ints[b'value'] == (want >= 3)


def test_the_side_buttons_step_between_fields_and_ok(fw, keys):
    ui = Ui(fw)
    top = time_window(ui)
    seen = []
    for key in [KEY_FWD_BTN] * 7 + [KEY_BACK_BTN] * 7:
        assert keys(key, 10) == STOP
        seen.append(field(top))
    assert seen == [1, 2, 3, 4, 5, 5, 5, 4, 3, 2, 1, 0, 0, 0]
    assert not fw.calls('stock_keyup_trampoline')  # not stock's track skip


def test_a_side_button_drops_a_pending_centre(fw, keys):
    ui = Ui(fw)
    top = time_window(ui)
    keys(KEY_CENTER)
    keys(KEY_FWD_BTN, 10)
    assert field(top) == 1
    keys.clock.now += O['DOUBLE_CLICK_MS']
    for timer in ui.due('navigation__edit_single'):
        ui.fire(timer=timer)
    assert field(top) == 1


@pytest.mark.parametrize('at, page, want', [(0, 1, 3), (2, 1, 3), (4, 0, 0), (3, 1, 3), (1, 0, 1)])
def test_a_swipe_between_pages_moves_to_that_pages_first_field(fw, keys, at, page, want):
    ui = Ui(fw)
    top = time_window(ui)
    top.ints[b'_edit_field'] = at
    top.find(b'slide_view').ints[b'value'] = page
    assert fw.fn('navigation__field_at', C.c_int, C.c_void_p)(top.p) == want


def test_the_sleep_timers_two_fields(fw, keys):
    ui = Ui(fw)
    top = time_window(ui, 'sleepshutdown_page', at=(0, 0, 0, 1, 30))
    top.find(b'time').add(W(ui, 'view', 'view_sleep'))
    keys(KEY_NEXT)
    assert index(top, 'tselector_hour') == 2
    centre(ui, keys)
    keys(KEY_PREV, 10)
    assert index(top, 'tselector_min') == 29
    centre(ui, keys)
    centre(ui, keys)
    assert ui.clicks == [(b'btn_enter', EVT_CLICK)]


def test_a_side_button_onto_the_sleep_timers_ok_drops_a_pending_centre(fw, keys):
    ui = Ui(fw)
    top = time_window(ui, 'sleepshutdown_page')
    top.find(b'time').add(W(ui, 'view', 'view_sleep'))
    top.ints[b'_edit_field'] = 1  # the minute, its last field
    keys(KEY_CENTER)
    keys(KEY_FWD_BTN, 10)
    assert field(top) == 2  # OK is next
    keys.clock.now += O['DOUBLE_CLICK_MS']
    for timer in ui.due('navigation__edit_single'):
        ui.fire(timer=timer)
    assert field(top) == 2 and ui.clicks == []  # the Centre from before the key doesn't press it
    centre(ui, keys)
    assert ui.clicks == [(b'btn_enter', EVT_CLICK)]  # a Centre of its own does


def no_toolbar(fw, st):
    """theme_boot's geometry set as an iPod theme's: no stock toolbar (geom_vals' toolbar 0)."""
    names = host.tools_theme.GEOM_VALS
    table = (C.c_short * (fw.sizes['theme_engine__geom_vals'] // 2)).in_dll(fw.lib, 'theme_engine__geom_vals')
    st('geom').value = next(g for g in range(len(table) // len(names)) if not table[g * len(names) + names.index('toolbar')])


def test_the_sleep_timer_switched_off_has_its_row_to_switch(fw, keys, st):
    no_toolbar(fw, st)
    ui = Ui(fw)
    top = time_window(ui, 'sleepshutdown_page')
    top.find(b'time').add(W(ui, 'view', 'view_sleep')).visible = 0  # only the switch's row shows
    top.add(W(ui, 'image', 'img_switch'))
    centre(ui, keys)
    assert ui.due('navigation__switch_due') and field(top) == 0


class Canvas:
    """A canvas and its lcd: the fill and stroke colours stock's setters write, and what's drawn."""
    def __init__(self, fw, lcd=True):
        self.raw, self.lcd = C.create_string_buffer(0x100), C.create_string_buffer(0x200)
        if lcd:
            struct.pack_into('<Q', self.raw, CANVAS_LCD, C.addressof(self.lcd))
        struct.pack_into('<II', self.lcd, LCD_FILL_COLOR, 0xff123456, 0xff654321)
        self.p, self.texts, self.art, self.drawn, self.placed = C.addressof(self.raw), [], [], [], []
        fw.mock('canvas_set_fill_color', lambda c, color, *_: struct.pack_into('<I', self.lcd, LCD_FILL_COLOR, color & 0xffffffff))
        fw.mock('canvas_set_stroke_color', lambda c, color, *_: struct.pack_into('<I', self.lcd, LCD_STROKE_COLOR, color & 0xffffffff))
        fw.mock('canvas_draw_text_in_rect', self.text)
        fw.mock('widget_load_image', self.image)
        fw.mock('canvas_draw_image', self.place)
        for name in ('canvas_stroke_rounded_rect', 'canvas_fill_rounded_rect', 'canvas_stroke_rect', 'canvas_fill_rect'):
            fw.mock(name, lambda *a, name=name: self.drawn.append(name) and 0)

    def text(self, c, s, n, r, *_):
        self.texts.append((''.join(map(chr, (C.c_uint * n).from_address(s))), struct.unpack_from('<4i', C.string_at(r, 16))))

    def image(self, w, name, bitmap, *_):
        self.art.append(C.string_at(name).decode())
        return 0

    def place(self, c, bitmap, src, dst, *_):
        """The image just loaded (paint_art), drawn with its top left at dst's."""
        x, y = struct.unpack_from('<2i', C.string_at(dst, 8)) if dst else (None, None)
        self.placed.append((self.art[-1] if self.art else None, x, y))

    def fill(self):
        return struct.unpack_from('<I', self.lcd, LCD_FILL_COLOR)[0]

    def stroke(self):
        return struct.unpack_from('<I', self.lcd, LCD_STROKE_COLOR)[0]


def edit_paint(fw, w, canvas):
    fw.fn('navigation__edit_paint', None, C.c_void_p, C.c_void_p)(w.p if w else None, canvas.p)


def test_the_edited_field_is_framed(fw, keys):
    ui = Ui(fw)
    top = time_window(ui)
    top.ints[b'_edit_field'] = 2
    canvas = Canvas(fw)
    edit_paint(fw, top.find(b'tselector_month'), canvas)
    assert canvas.drawn == []
    edit_paint(fw, top.find(b'tselector_day'), canvas)
    assert canvas.drawn and canvas.stroke() == 0xff654321  # framed, the stroke given back


def test_ok_when_next_is_a_selected_row_with_its_label(fw, keys):
    ui = Ui(fw)
    top = time_window(ui)
    top.ints[b'_edit_field'] = 5
    canvas = Canvas(fw)
    edit_paint(fw, top.find(b'btn_enter'), canvas)
    assert [t for t, _ in canvas.texts] == ['OK'] and canvas.fill() == 0xff123456 and 'canvas_fill_rounded_rect' in canvas.drawn


@pytest.mark.parametrize('size', [(4, 40), (40, 4)])
def test_a_field_too_small_to_frame_is_left(fw, keys, size):
    ui = Ui(fw)
    top = time_window(ui)
    w = top.find(b'tselector_year')
    struct.pack_into('<ii', w.block, O['W_W'], *size)
    canvas = Canvas(fw)
    edit_paint(fw, w, canvas)
    assert canvas.drawn == []


def test_no_lcd_no_frame(fw, keys):
    ui = Ui(fw)
    top = time_window(ui)
    canvas = Canvas(fw, lcd=False)
    edit_paint(fw, top.find(b'tselector_year'), canvas)
    assert canvas.drawn == []


# Classic's Set Date and Set Time.
@pytest.fixture
def classic(fw):
    Config(fw, THEME=b'1')  # IPOD THEME 1, Classic (assets/themes/classic-light)


def open_time(fw, ui, twelve=True, at=(4, 2, 4, 21, 5)):
    fw.var('g_time24h_flag', C.c_ubyte).value = not twelve
    top = time_window(ui, at=at)
    nav = top.add(W(ui, 'view', 'view_navbar'))
    title = nav.add(W(ui, 'label', 'title'))
    fw.fn('navigation__time_open', None, C.c_void_p)(top.p)
    fw.fn('ringnav_manualtime', C.c_int, C.c_void_p, C.c_void_p)(top.p, None)
    assert fw.fn('navigation__time_form', C.c_int, C.c_void_p)(top.p) == 1
    return top, title


DATE = {b'tselector_year': 2022, b'tselector_month': 1, b'tselector_day': 1}  # what each stock selector's option 0 means


def held(top):
    """The date the selectors hold: its month and its day and year."""
    year, month, day = (top.find(n).int(SELECTOR_INDEX) + first for n, first in DATE.items())
    return month, sorted([day, year])


def named(title):
    """The date a title names: its month, by name, and its numbers."""
    text = title.utf8.decode()
    months = [m for m in range(1, 13) if calendar.month_abbr[m] in text]
    assert len(months) == 1, text
    return months[0], sorted(int(n) for n in re.findall(r'\d+', text))


def test_classic_titles_the_page_with_the_date_its_selectors_hold(fw, keys, classic):
    ui = Ui(fw)
    top, title = open_time(fw, ui)
    month, numbers = named(title)
    assert (month, numbers) == held(top)
    top.ints[b'_edit_field'] = 1
    keys(KEY_NEXT)
    assert named(title) == held(top) == (month + 1, numbers)  # the month turned on, the day and year kept


def test_classic_lays_its_selectors_over_its_painted_cells(fw, keys, classic):
    ui = Ui(fw)
    top, _ = open_time(fw, ui)
    names = ['tselector_year', 'tselector_month', 'tselector_day', 'tselector_hour', 'tselector_min']
    boxes = [top.find(n.encode()).box() for n in names]
    assert all(top.find(n.encode()).opacity == 0 for n in names)  # still the truth, under the paint
    assert len({b[1] for b in boxes}) == 1 and boxes[0][2] > boxes[1][2] == boxes[2][2]  # one row; the year wider
    assert boxes[0][0] < boxes[1][0] < boxes[2][0] and boxes[3][0] < boxes[4][0]
    assert not top.find(b'img_dot').visible and not top.find(b'slide_indicator').visible
    assert top.find(b'btn_enter').opacity == 0  # painted too: its box is the touch


def test_classic_twelve_hour_has_an_am_pm_cell_that_crosses_noon(fw, keys, classic):
    ui = Ui(fw)
    top, _ = open_time(fw, ui, at=(4, 2, 4, 9, 5))
    ampm = [w for w in top.find(b'time').children if w.kind == b'image']
    assert len(ampm) == 1
    ui.emit(ampm[0], EVT_CLICK)
    assert index(top, 'tselector_hour') == 21 and field(top) == 5
    ui.emit(ampm[0], EVT_CLICK)
    assert index(top, 'tselector_hour') == 9


def test_classic_twenty_four_hour_has_no_am_pm(fw, keys, classic):
    ui = Ui(fw)
    top, _ = open_time(fw, ui, twelve=False)
    assert not [w for w in top.find(b'time').children if w.kind == b'image']
    assert fw.fn('navigation__fields', C.c_int, C.c_void_p)(top.p) == 5


@pytest.mark.parametrize('start, key, want', [(23, KEY_NEXT, 12), (12, KEY_PREV, 23), (11, KEY_NEXT, 0), (0, KEY_PREV, 11), (15, KEY_NEXT, 16)])
def test_classic_twelve_hour_wheel_keeps_the_hours_half(fw, keys, classic, start, key, want):
    ui = Ui(fw)
    top, _ = open_time(fw, ui, at=(4, 2, 4, start, 5))
    top.ints[b'_edit_field'] = 3
    top.find(b'slide_view').ints[b'value'] = 1
    keys(key)
    assert index(top, 'tselector_hour') == want


def test_classic_twenty_four_hour_wheel_runs_the_whole_day(fw, keys, classic):
    ui = Ui(fw)
    top, _ = open_time(fw, ui, twelve=False, at=(4, 2, 4, 11, 5))
    top.ints[b'_edit_field'] = 3
    top.find(b'slide_view').ints[b'value'] = 1
    keys(KEY_NEXT)
    assert index(top, 'tselector_hour') == 12


def test_classic_wheel_stops_at_a_fields_ends(fw, keys, classic):
    ui = Ui(fw)
    top, _ = open_time(fw, ui, at=(0, 11, 4, 9, 5))
    top.ints[b'_edit_field'] = 1
    keys(KEY_NEXT)
    assert index(top, 'tselector_month') == 11 and not fw.calls('text_selector_set_selected_index')
    top.ints[b'_edit_field'] = 0
    keys(KEY_PREV, 10)
    assert index(top, 'tselector_year') == 0


@pytest.mark.parametrize('twelve, last', [(True, 5), (False, 4)])
def test_classic_centre_saves_after_the_last_field(fw, keys, classic, twelve, last):
    ui = Ui(fw)
    top, _ = open_time(fw, ui, twelve=twelve)
    seen = []
    for _ in range(last):
        centre(ui, keys)
        seen.append(field(top))
    assert seen == list(range(1, last + 1)) and ui.clicks == []
    centre(ui, keys)
    assert ui.clicks == [(b'btn_enter', EVT_CLICK)]  # no OK stop: Save


def test_classic_side_buttons_stop_at_the_last_field(fw, keys, classic):
    ui = Ui(fw)
    top, _ = open_time(fw, ui)
    for _ in range(8):
        keys(KEY_FWD_BTN, 10)
    assert field(top) == 5


def test_a_drag_takes_the_selection_to_its_field(fw, keys, classic):
    ui = Ui(fw)
    top, title = open_time(fw, ui)
    day = top.find(b'tselector_day')
    day.int(SELECTOR_INDEX, day.int(SELECTOR_INDEX) + 5)
    ui.emit(day, EVT_VALUE_CHANGED)
    assert field(top) == 2 and named(title) == held(top)  # the title follows the drag


def test_a_twelve_hour_drag_keeps_the_hours_half(fw, keys, classic):
    ui = Ui(fw)
    top, _ = open_time(fw, ui, at=(4, 2, 4, 21, 5))  # 9 PM
    hour = top.find(b'tselector_hour')
    hour.int(SELECTOR_INDEX, 8)  # the drag landed on 8, in the AM half
    ui.emit(hour, EVT_VALUE_CHANGED)
    assert index(top, 'tselector_hour') == 20 and field(top) == 3


def test_a_swipe_alone_changes_no_field(fw, keys, classic):
    ui = Ui(fw)
    top, _ = open_time(fw, ui)
    top.ints[b'_edit_field'] = 1
    ui.emit(top.find(b'slide_view'), EVT_VALUE_CHANGED)
    assert field(top) == 1


def test_the_page_closing_forgets_it(fw, keys, classic, st):
    ui = Ui(fw)
    top, _ = open_time(fw, ui)
    assert st('slider_win', C.c_void_p).value == top.p
    ui.emit(top, EVT_DESTROY)
    assert not st('slider_win', C.c_void_p).value


def time_paint(fw, top, canvas):
    fw.fn('navigation__time_paint', None, C.c_void_p, C.c_void_p)(top.p, canvas.p)


def in_box(canvas, w):
    """The text painted in w's box: a selector's cell (time_open put it there), or AM/PM's image."""
    return [t for t, r in canvas.texts if r == w.box()]


@pytest.mark.parametrize('edited', range(len(DATE)))
def test_classic_paints_the_date_page(fw, keys, classic, edited):
    ui = Ui(fw)
    top, _ = open_time(fw, ui, at=(4, 2, 4, 21, 5))
    top.ints[b'_edit_field'] = edited
    canvas = Canvas(fw)
    time_paint(fw, top, canvas)
    cells = [top.find(n) for n in DATE]
    assert [[int(t) for t in in_box(canvas, w)] for w in cells] == [[w.int(SELECTOR_INDEX) + first] for w, first in zip(cells, DATE.values())]
    assert calendar.month_abbr[held(top)[0]] in [t for t, _ in canvas.texts]  # the tile's month
    lit = [(art, x) for art, x, _ in canvas.placed if art and '_lit_' in art]
    assert len(lit) == 1
    art, x = lit[0]
    assert min(cells, key=lambda w: abs(w.box()[0] - x)) is cells[edited]  # over the edited field
    assert art.endswith('_%d' % cells[edited].box()[2])  # its art cut to the field's width
    assert canvas.fill() == 0xff123456


@pytest.mark.parametrize('twelve, hour, shown', [(True, 21, ['9', '05', 'PM']), (True, 0, ['12', '05', 'AM']), (True, 12, ['12', '05', 'PM']),
                                                 (False, 21, ['21', '05'])])
def test_classic_paints_the_time_page(fw, keys, classic, twelve, hour, shown):
    ui = Ui(fw)
    top, _ = open_time(fw, ui, twelve=twelve, at=(4, 2, 4, hour, 5))
    top.find(b'slide_view').ints[b'value'] = 1
    top.ints[b'_edit_field'] = 3
    canvas = Canvas(fw)
    time_paint(fw, top, canvas)
    boxes = [top.find(b'tselector_hour'), top.find(b'tselector_min')] + [w for w in top.find(b'time').children if w.kind == b'image']
    assert [t for w in boxes for t in in_box(canvas, w)] == shown
    ok = top.find(b'btn_enter')
    assert in_box(canvas, ok) == [ok.utf8.decode()]  # the hidden button's label, painted in its box
    assert ':' in [t for t, _ in canvas.texts] and len([a for a in canvas.art if '_lit_' in a]) == 1


def test_classic_paints_nothing_without_an_lcd(fw, keys, classic):
    ui = Ui(fw)
    top, _ = open_time(fw, ui)
    canvas = Canvas(fw, lcd=False)
    time_paint(fw, top, canvas)
    assert canvas.texts == [] and canvas.art == []


def test_classic_frames_no_field_itself(fw, keys, classic):
    ui = Ui(fw)
    top, _ = open_time(fw, ui)
    canvas = Canvas(fw)
    edit_paint(fw, top.find(b'tselector_year'), canvas)
    assert canvas.drawn == []  # its cells are painted lit instead


# The UI loop's pass (ringnav_sleep) and the input hook (ringnav_input).
class Loop:
    """A UI loop pass over a page, the clock and stock's sleeps watched; stock's own pass returns 42."""
    def __init__(self, fw, stock_bytes, lit=True):
        for at in (O['KEY_LOCKOUT'], O['BOOT_KEY_GUARD'], O['RETURN_RELEASE_LATCH']):
            stock_bytes(at, 1)
        self.idle = stock_bytes(O['BC_IDLE'], 1)
        self.fw, self.clock, self.ui = fw, Clock(fw, 1000000), Ui(fw, lit)
        self.page = self.ui.window('about_page')
        fw.var('fw_ctx_g_dacoff_time', C.c_int).value = -1  # the DAC is off
        fw.mock('stock_sleep_trampoline', lambda *_: 42)
        Files(fw, {})
        self.run = fw.fn('ringnav_sleep', C.c_int, C.c_void_p)

    def __call__(self, after=0):
        self.clock.now += after
        calls = len(self.fw.calls())
        assert self.run(None) == 42
        return [c[1][0] for c in self.fw.calls()[calls:] if c[0] == 'sleep_ms']

    def dark_quiet(self):
        return C.c_int.in_dll(self.fw.lib, 'navigation__dark_quiet').value


def test_dark_and_quiet_the_loop_idles_long(fw, stock_bytes, st):
    loop = Loop(fw, stock_bytes, lit=False)
    assert loop() == [O['DARK_IDLE_SLEEP_MS']] and loop.dark_quiet() == 1 and st('dark').value & 1


@pytest.mark.parametrize('why', ['playing', 'dac', 'wake', 'airplay', 'receiver'])
def test_dark_but_busy_the_loop_idles_short(fw, stock_bytes, st, why):
    loop = Loop(fw, stock_bytes, lit=False)
    if why == 'playing':
        fw.mock('mclGetPlayStatus', lambda *_: 2)
    elif why == 'dac':
        fw.var('fw_ctx_g_dacoff_time', C.c_int).value = 30  # counting down to the DAC's power-off
    elif why == 'wake':
        st('wake_at', C.c_uint).value = loop.clock.now - O['WAKE_MS'] + 1  # Wake's second press may come
    elif why == 'airplay':
        fw.mock('airplayGetFlag', lambda *_: 2)
    else:
        fw.var('bt__recv_pageflag', C.c_ubyte).value = 2
    assert loop() == [O['SCREEN_OFF_SLEEP_MS']] and loop.dark_quiet() == 0


def test_a_dark_wheel_turn_keeps_stocks_pace(fw, stock_bytes, st):
    loop = Loop(fw, stock_bytes, lit=False)
    st('tick_at', C.c_uint).value = loop.clock.now  # tick_gate's stamp, this very millisecond
    assert loop() == [] and loop.dark_quiet() == 1
    assert loop(O['DARK_TURN_MS'] - 1) == []
    assert loop(1) == [O['DARK_IDLE_SLEEP_MS']]


def test_dark_a_pass_clicks_once(fw, stock_bytes):
    loop, buzzer = Loop(fw, stock_bytes, lit=False), Buzzer(fw, lit=False)
    buzzer.click(1), buzzer.click(1)  # two ticks one idle slept through
    assert buzzer.clicks() == [1]
    loop()
    buzzer.click(1)
    assert buzzer.clicks() == [1, 1]


def test_a_dark_click_that_cant_start_leaves_the_pass_its_click(fw, stock_bytes):
    Loop(fw, stock_bytes, lit=False)
    buzzer = Buzzer(fw, 'fail', lit=False)
    buzzer.click(1)
    buzzer.mode = 'run'
    buzzer.click(1)
    assert buzzer.clicks() == [1]


class QuickHold:
    """Return's hold (compact_now_playing) with Quick Settings pulled down over the loop's page."""
    def __init__(self, fw, loop):
        self.fw, self.loop, self.opened = fw, loop, []
        Config(fw)
        self.quick = loop.ui.window('statusbar_dialog', 'dialog')
        fw.mock('navigator_switch_to_with_context', lambda name, *_: self.opened.append(C.string_at(name)) and 0)
        assert fw.fn('compact_now_playing', C.c_int)() == 0
        assert [c[1][0] for c in fw.calls('dialog_quit')] == [self.quick.p] and self.opened == []

    def close(self):
        self.loop.ui.top = self.loop.page  # the dialog's own idle closed it


def test_a_held_return_over_quick_settings_closes_it_then_acts_on_the_page_under(fw, stock_bytes, st):
    loop = Loop(fw, stock_bytes)
    hold = QuickHold(fw, loop)
    loop()  # the same millisecond, the dialog still up
    assert hold.opened == []
    hold.close()
    loop.ui.animating = 1
    loop(20)
    assert hold.opened == []  # its close still sliding
    loop.ui.animating = 0
    loop(20)
    assert hold.opened == [b'playing_page'] and st('np_origin', C.c_void_p).value == loop.page.p
    loop(20)
    assert hold.opened == [b'playing_page'] and len(fw.calls('dialog_quit')) == 1


def test_a_quit_that_quick_settings_refuses_is_no_hold_to_wait_for(fw, stock_bytes, st):
    loop = Loop(fw, stock_bytes)
    Config(fw)
    loop.ui.window('statusbar_dialog', 'dialog')
    fw.mock('dialog_quit', lambda *_: 0x10)  # RET_BAD_PARAMS: already quit, or not open yet
    assert fw.fn('compact_now_playing', C.c_int)() == 0 and not st('qs_hold', C.c_uint).value


def test_a_held_return_over_shut_down_cancels_it_and_opens_nothing(fw, stock_bytes):
    loop = Loop(fw, stock_bytes)
    Config(fw)
    fw.var('g_poweroff_state', C.c_ubyte).value = 1
    prompt = loop.ui.window('confirminfo_dialog', 'dialog')
    opened = []
    fw.mock('navigator_switch_to_with_context', lambda name, *_: opened.append(C.string_at(name)) and 0)
    fw.mock('navigator_back_to_home', lambda *_: opened.append(b'home') and 0)
    assert fw.fn('compact_now_playing', C.c_int)() == 0
    assert [c[1][:2] for c in fw.calls('dialog_quit')] == [(prompt.p, 4)] and opened == []


def test_a_hold_whose_quick_settings_stayed_up_is_forgotten(fw, stock_bytes, st):
    loop = Loop(fw, stock_bytes)
    hold = QuickHold(fw, loop)
    loop(O['QS_HOLD_MS'] - 1)
    assert st('qs_hold', C.c_uint).value  # still waited for
    loop(1)
    hold.close()
    loop(1)
    assert hold.opened == [] and not st('qs_hold', C.c_uint).value


def test_the_first_lit_pass_repaints_the_whole_screen_once(fw, stock_bytes, st):
    loop = Loop(fw, stock_bytes, lit=False)
    loop()
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    st('last_input', C.c_uint).value = loop.clock.now
    loop.ui.invalidated.clear()
    assert loop(10) == [] and loop.dark_quiet() == 0 and st('dark').value == 0
    assert loop.ui.invalidated.count(loop.ui.wm.p) == 1
    loop(10)
    assert loop.ui.invalidated.count(loop.ui.wm.p) == 1


def test_a_centre_that_woke_the_screen_restarts_its_wake_window(fw, stock_bytes, st):
    loop = Loop(fw, stock_bytes, lit=False)
    loop()
    st('dark').value |= 2  # a passing dark Centre press
    st('woke_at', C.c_uint).value = 5
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    loop(300)  # turning the screen on took this long
    assert st('woke_at', C.c_uint).value == loop.clock.now


def test_a_dark_pass_woken_by_another_key_leaves_the_wake_window(fw, stock_bytes, st):
    loop = Loop(fw, stock_bytes, lit=False)
    loop()
    st('woke_at', C.c_uint).value = 5
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    loop(300)
    assert st('woke_at', C.c_uint).value == 5


def test_a_lit_idle_pass_sleeps_once_input_has_been_still(fw, stock_bytes, st):
    loop = Loop(fw, stock_bytes)
    st('last_input', C.c_uint).value = loop.clock.now
    assert loop(O['IDLE_MS'] - 1) == []
    assert loop(1) == [O['IDLE_SLEEP_MS']]


@pytest.mark.parametrize('busy', ['animating', 'marquee'])
def test_a_lit_pass_stays_awake_while_something_moves(fw, stock_bytes, st, busy):
    loop = Loop(fw, stock_bytes)
    if busy == 'animating':
        loop.ui.animating = 1
    else:
        loop.ui.window('playing_page')
        st('np_marquee', C.c_uint).value, st('np_off').value, st('np_over').value = 9, 10, 60  # the album scrolling
    assert loop(O['IDLE_MS']) == []


def test_the_big_art_stays_up_off_the_art_page(fw, stock_bytes, st):
    loop = Loop(fw, stock_bytes)
    slide = loop.page.add(W(loop.ui, 'slide_view', 'np_slide', value=1))
    st('np_slide', C.c_void_p).value = slide.p
    loop()
    assert C.c_byte.from_address(loop.idle).value == O['BC_IDLE_FULL']


def test_a_resume_at_the_standby_alarm_powers_off_from_the_next_pass(fw, stock_bytes, st):
    loop = Loop(fw, stock_bytes, lit=False)
    rtc = Rtc(fw)
    rtc.files['/proc/mounts'] = b''
    standby(fw, rtc, 2, LIMITS[2] * 3600)
    shown = []
    fw.mock('navigator_to', lambda name, *_: shown.append(C.string_at(name)) and 0)
    loop()
    assert shown == [b'poweroff_page']


def test_every_pass_feeds_the_watchdog(fw, stock_bytes, power):
    loop = Loop(fw, stock_bytes)
    power('wdt').value = 7
    loop(), loop(10)
    assert [c[1][:2] for c in fw.calls('fw_ioctl')] == [(7, WDT_FEED)] * 2


class Input:
    """ringnav_input, the window manager's input hook, with stock's dispatch watched."""
    def __init__(self, fw, stock_bytes, ui=None):
        for at in (O['KEY_LOCKOUT'], O['BOOT_KEY_GUARD'], O['RETURN_RELEASE_LATCH']):
            stock_bytes(at, 1)
        self.fw, self.clock = fw, Clock(fw, 500000)
        self.ui = ui or Ui(fw)
        if not self.ui.top:
            self.ui.window('about_page')
        self.stock = []
        fw.mock('stock_input_trampoline', lambda wm, e, *_: self.stock.append(
            (C.c_int.from_address(e + EVENT_TYPE).value, C.c_int.from_address(e + EVENT_KEY).value) if e else None) and 0)
        self.send = fw.fn('ringnav_input', C.c_int, C.c_void_p, C.c_void_p)
        self.frame = fw.var('wheel_frame_at', C.c_uint)

    def __call__(self, kind, key, after=0, x=0, y=0):
        self.clock.now += after
        e = key_event(key, kind)
        if kind not in (DOWN, UP, LONG):
            struct.pack_into('<ii', e, O['EVENT_X'], x, y)
        return self.send(self.ui.wm.p, e)


@pytest.mark.parametrize('age, passes', [(0, True), (O['WHEEL_FRESH_MS'] - 1, True), (O['WHEEL_FRESH_MS'], False), (-5, True)])
def test_a_wheel_tick_needs_a_finger_on_the_ring(fw, stock_bytes, st, age, passes):
    keys = Input(fw, stock_bytes)
    keys.frame.value = keys.clock.now - age  # -5: a frame stamped after the clock was read
    keys(DOWN, KEY_NEXT), keys(UP, KEY_NEXT, 20)
    assert keys.stock == ([(DOWN, KEY_NEXT), (UP, KEY_NEXT)] if passes else [])
    assert (st('last_input', C.c_uint).value == keys.clock.now) == passes  # a dropped tick doesn't time the idle
    assert (st('tick_at', C.c_uint).value == keys.clock.now - 20) == passes  # nor a dark turn's pace


def test_a_dropped_ticks_release_goes_with_it_even_once_a_finger_is_back(fw, stock_bytes):
    keys = Input(fw, stock_bytes)
    keys.frame.value = keys.clock.now - 5000
    keys(DOWN, KEY_PREV)
    keys.frame.value = keys.clock.now
    keys(UP, KEY_PREV, 10)
    keys(DOWN, KEY_PREV, 10)
    assert keys.stock == [(DOWN, KEY_PREV)]


def test_returns_stop_mutes_the_ticks(fw, stock_bytes):
    keys = Input(fw, stock_bytes)
    keys.frame.value = keys.clock.now
    fw.var('wheel_mute', C.c_int).value = 1
    keys(DOWN, KEY_NEXT), keys(UP, KEY_NEXT)
    assert keys.stock == []


def test_other_keys_pass_without_a_frame(fw, stock_bytes):
    keys = Input(fw, stock_bytes)
    keys(DOWN, KEY_PLAY), keys(UP, KEY_PLAY, 10)
    assert keys.stock == [(DOWN, KEY_PLAY), (UP, KEY_PLAY)]


@pytest.mark.parametrize('kind', [O['EVT_POINTER_FIRST'], O['EVT_POINTER_DOWN'], O['EVT_POINTER_ABORT']])
def test_a_finger_is_noted_for_stale_heal(fw, stock_bytes, st, kind):
    keys = Input(fw, stock_bytes)
    keys(kind, 0, 7, x=120, y=210)
    assert (st('touch_at', C.c_uint).value, st('touch_type').value, st('touch_x').value, st('touch_y').value) == \
        (keys.clock.now, kind, 120, 210)
    assert keys.stock == [(kind, 120)]


def test_a_playing_video_takes_key_releases_and_nothing_reaches_the_ui(fw, stock_bytes, st):
    keys = Input(fw, stock_bytes)
    fw.var('books__vid', Vid).pid = 77
    fw.var('g_volume', C.c_ubyte).value, fw.var('g_maxvolume', C.c_ubyte).value = 30, 100
    keys(DOWN, KEY_PLAY), keys(UP, KEY_PLAY, 10)
    keys.frame.value = keys.clock.now
    keys(DOWN, KEY_NEXT), keys(UP, KEY_NEXT, 10)
    assert keys.stock == [] and fw.var('g_volume', C.c_ubyte).value == 31  # the player's volume step
    assert st('last_input', C.c_uint).value == keys.clock.now


def test_a_card_game_takes_its_keys_but_not_the_musics(fw, stock_bytes):
    keys = Input(fw, stock_bytes)
    fw.var('addon__gm', C.c_int).value = 78
    keys(DOWN, KEY_CENTER), keys(DOWN, KEY_PLAY, 10)
    assert keys.stock == [(DOWN, KEY_PLAY)]


def usb_prompt(fw, stock_bytes):
    """USB mode's scan prompt, its cable out: a BUTTONS dialog, its two buttons its rows."""
    ui = Ui(fw)
    top = ui.window('confirminfo_dialog', 'dialog')
    for name in ('btn_yes', 'btn_no'):
        top.add(W(ui, 'button', name, w=150, click=True))
    keys = Input(fw, stock_bytes, ui)
    fw.var('g_usblink_status', C.c_ubyte).value = 2
    keys.frame.value = keys.clock.now
    return ui, top, keys


def test_the_usb_scan_prompt_takes_the_wheel(fw, stock_bytes):
    ui, top, keys = usb_prompt(fw, stock_bytes)
    select(fw, top, 0)
    keys(DOWN, KEY_NEXT), keys(UP, KEY_NEXT, 10)
    assert keys.stock == [] and top.ints[SEL] == 1  # the release moved it, through ringnav
    assert len(fw.calls('reset_poweroptions_timer')) == 2 and not fw.calls('stock_keyup_trampoline')


def test_the_usb_scan_prompt_takes_centre(fw, stock_bytes):
    ui, top, keys = usb_prompt(fw, stock_bytes)
    select(fw, top, 1)
    keys(DOWN, KEY_CENTER), keys(UP, KEY_CENTER, 10)
    assert keys.stock == [] and ui.due('navigation__confirm_center')
    keys.clock.now += O['DOUBLE_CLICK_MS']
    ui.fire('navigation__confirm_center')
    assert ui.clicks == [(b'btn_no', EVT_CLICK)]


def test_with_the_cable_in_the_prompt_keeps_stocks_keys(fw, stock_bytes):
    ui, top, keys = usb_prompt(fw, stock_bytes)
    fw.var('g_usbdet_value', C.c_ubyte).value = 1
    keys(DOWN, KEY_NEXT), keys(UP, KEY_NEXT, 10)
    assert keys.stock == [(DOWN, KEY_NEXT), (UP, KEY_NEXT)]


class Np:
    """Now Playing on top, sliding, the wheel's ticks still queued as list ticks."""
    def __init__(self, fw, stock_bytes, volume, limit):
        self.ui = Ui(fw)
        self.ui.window('playing_page')
        self.ui.animating = 1
        self.keys = Input(fw, stock_bytes, self.ui)
        fw.var('g_volume', C.c_ubyte).value, fw.var('g_maxvolume', C.c_ubyte).value = volume, limit


@pytest.mark.parametrize('volume, limit, key, want', [(30, 100, KEY_NEXT, 31), (30, 100, KEY_PREV, 29), (0, 100, KEY_PREV, 0),
                                                      (80, 80, KEY_NEXT, 80), (100, 120, KEY_NEXT, 100)])
def test_ticks_queued_into_a_sliding_now_playing_are_volume(fw, stock_bytes, st, volume, limit, key, want):
    np = Np(fw, stock_bytes, volume, limit)
    np.keys.frame.value = np.keys.clock.now
    st('held').value = 3
    np.keys(UP, key)
    assert fw.var('g_volume', C.c_ubyte).value == want and np.keys.stock == [] and st('held').value == 0
    assert [c[1][:2] for c in fw.calls('device_set_volume')] == ([] if want == volume else [(want, 1)])
    assert len(fw.calls('write_int_config')) == (want != volume)


@pytest.mark.parametrize('front, y, want', [('confirminfo_dialog', 5, O['QS_PULL_Y']), ('confirminfo_dialog', 200, 200),
                                            ('home_page', 5, 5)])
def test_a_touch_on_a_prompt_starts_below_the_pull_downs_band(fw, stock_bytes, front, y, want):
    """Quick Settings over Shut Down?, then Return, left the prompt up with dead buttons (#303)."""
    ui = Ui(fw)
    ui.window(front)
    keys = Input(fw, stock_bytes, ui)
    start = C.c_int.from_address(C.addressof(fw.var('fw_ctx_g_pressdown_y', C.c_char)))
    fw.mock('stock_input_trampoline', lambda wm, e, *_: setattr(start, 'value', C.c_int.from_address(e + O['EVENT_Y']).value) or 7)
    assert keys(O['EVT_POINTER_FIRST'], 0, y=y) == 7 and start.value == want


@pytest.mark.parametrize('lit', [0, 1])
def test_a_tick_before_the_volume_dialog_takes_keys_steps_it_and_opens_no_other(fw, keys, lit):
    """The dialog is in front from navigator_to on, the window manager's key target only from its open event (#303)."""
    ui = Ui(fw)
    ui.window('home_page')
    vol = ui.window('volume_dialog')
    slider = vol.add(W(ui, 'slider', 'slider_vol', value=31))
    fw.var('g_volume', C.c_ubyte).value, fw.var('g_maxvolume', C.c_ubyte).value = 31, 100
    fw.var('g_backlight_status', C.c_ubyte).value = lit
    target = C.c_void_p.from_address(ui.wm.p + O['W_KEY_TARGET'])
    assert keys(KEY_PREV) == STOP and keys(KEY_PREV, 20) == STOP and keys(KEY_NEXT, 20) == STOP  # one dark pass's ticks
    assert fw.var('g_volume', C.c_ubyte).value == 30 == slider.ints[b'value']
    assert [c[1][:2] for c in fw.calls('device_set_volume')] == [(30, 1), (29, 1), (30, 1)]
    target.value = vol.p  # opened: the ticks are the dialog's own
    assert keys(KEY_PREV, 20) != STOP and fw.var('g_volume', C.c_ubyte).value == 30


def test_a_locked_wheels_tick_before_the_volume_dialog_takes_keys_stays_stocks(fw, keys):
    ui = Ui(fw)
    ui.window('volume_dialog')
    fw.var('g_volume', C.c_ubyte).value = 31
    fw.mock('stock_keyup_trampoline', lambda *_: STOP)  # stock's dark lock filter stops it
    assert keys(KEY_NEXT) == STOP and fw.var('g_volume', C.c_ubyte).value == 31 and not fw.calls('device_set_volume')


def test_a_settled_now_playing_gets_its_ticks(fw, stock_bytes):
    np = Np(fw, stock_bytes, 30, 100)
    np.ui.animating = 0
    np.keys.frame.value = np.keys.clock.now
    np.keys(UP, KEY_NEXT)
    assert np.keys.stock == [(UP, KEY_NEXT)] and fw.var('g_volume', C.c_ubyte).value == 30


def test_no_event_still_times_the_idle(fw, stock_bytes, st):
    keys = Input(fw, stock_bytes)
    fw.fn('ringnav_input', C.c_int, C.c_void_p, C.c_void_p)(keys.ui.wm.p, None)
    assert st('last_input', C.c_uint).value == keys.clock.now and len(fw.calls('stock_input_trampoline')) == 1


def test_a_failed_songtable_check_ends_the_pass_unwritten(fw, st):
    lib = Library(fw)
    lib.song(b'/mnt/mmc/a.flac')
    lib.rate(b'/mnt/mmc/a.flac', 3)
    real = lib.exec_db
    fw.mock('sqlite3_exec', lambda db, sql, *a: 1 if b'sqlite_master' in C.string_at(sql) else real(db, sql, *a))
    assert lib.run() and lib.copy() is None and fw.calls('sqlite3_close')


# More of the key release: Home's main list, the media keys, the restart toast, a carousel page.
def test_return_on_homes_list_selects_now_playing_then_opens_it(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui, 'home_page', rows=6)
    select(fw, sv, 3)
    assert keys(KEY_RETURN) == STOP and sv.ints[SEL] == 0 and not fw.calls('navigator_switch_to_with_context')
    assert keys(KEY_RETURN, 300) == STOP
    assert [C.string_at(c[1][0]) for c in fw.calls('navigator_switch_to_with_context')] == [b'playing_page']


def test_homes_list_bumps_and_wraps_as_a_ring_list(fw, keys):
    """docs/ipod.md#home: though contexts.inc doesn't mark home_page a ring."""
    ui = Ui(fw)
    sv = list_page(ui, 'home_page', rows=6)
    select(fw, sv, 5)
    keys(KEY_NEXT)
    assert sv.ints[SEL] == 5
    keys(KEY_NEXT, O['EDGE_PAUSE_MS'])
    assert sv.ints[SEL] == 0


def test_play_follows_a_playing_video(fw, keys):
    ui = Ui(fw)
    list_page(ui)
    vid = fw.var('books__vid', Vid)
    vid.pid = 77
    assert keys(KEY_PLAY) == STOP and vid.paused == 1 and not fw.calls('player_play_pause')


@pytest.mark.parametrize('age, skipped', [(0, 1), (3000, 1), (3001, 0)])
def test_a_second_next_on_the_restart_toast_skips(fw, keys, age, skipped):
    ui = Ui(fw)
    ui.window('msginfo_dialog', 'dialog')
    fw.var('playback__restart_at', C.c_uint).value = keys.clock.now - age  # the first Next asked
    fw.var('playback__restart_key', C.c_int).value = 1
    assert (keys(KEY_FWD_BTN) == STOP) == bool(skipped) and len(fw.calls('next_quick_click')) == skipped
    assert not fw.calls('prev_quick_click')


def slides_page(ui, name='games_page', n=3, at=1):
    top = ui.window(name)
    menu = top.add(W(ui, 'slide_menu', 'menu', h=200))
    for i in range(n):
        menu.add(W(ui, 'button', 'slide%d' % i, w=100, h=100, click=True))
    menu.int(O['SLIDE_INDEX'], at)
    return menu


@pytest.mark.parametrize('key, stub', [(KEY_NEXT, 'slide_menu_scroll_to_next'), (KEY_PREV, 'slide_menu_scroll_to_prev')])
def test_a_slide_menu_page_steps_its_slides(fw, keys, key, stub):
    ui = Ui(fw)
    menu = slides_page(ui)
    assert keys(key) == STOP and [c[1][0] for c in fw.calls(stub)] == [menu.p]


def test_an_overdue_click_that_locks_the_screen_drops_the_next_centre(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui)
    select(fw, sv, 1)
    keys(KEY_CENTER)
    def lock(target, event, *_):
        ui.dispatch(target, event)
        fw.var('g_lockscreen_pageflag', C.c_ubyte).value = 1  # the row's own action locked it
        return 0
    fw.mock('stock_dispatch_trampoline', lock)
    assert keys(KEY_CENTER, O['DOUBLE_CLICK_MS'] + 50) == 0
    assert ui.clicks == [(b'row1', EVT_CLICK)] and not ui.due('navigation__confirm_center')


def test_a_lone_centre_on_now_playing_turns_the_screen_off(fw, keys, st):
    ui = Ui(fw)
    top = ui.window('playing_page')
    st('np_win', C.c_void_p).value = top.p
    assert keys(KEY_CENTER) == STOP and not fw.calls('on_wm_keyup_fun')  # a second press may come: a scrub
    assert [ui.timers[t][2] for t in ui.due('navigation__np_single')] == [O['DOUBLE_CLICK_MS']]
    ui.fire('navigation__np_single')
    assert [C.c_int.from_address(c[1][1] + EVENT_KEY).value for c in fw.calls('on_wm_keyup_fun')] == [KEY_CENTER]


def test_a_wheel_tick_after_an_overdue_centre_drops_its_click(fw, keys):
    ui = Ui(fw)
    sv = list_page(ui)
    select(fw, sv, 1)
    keys(KEY_CENTER)
    keys(KEY_NEXT, O['DOUBLE_CLICK_MS'] + 50)
    assert ui.clicks == [] and sv.ints[SEL] == 2
