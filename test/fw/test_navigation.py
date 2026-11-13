"""patch/navigation.c, tags.c and library_lists.c: tag and CUE text, comma lists, Light Mode's colours, Era's and the playlists' rows, config."""
import ctypes as C
import datetime
import random
import re
import struct

import pytest

import host
from conftest import text, Game, c_enum
from conftest import FNV_PRIME, O, Deques, Files, claim, fnv1a, in_fork  # noqa: F401

STOCK_RED = O['STOCK_RED']
POD = c_enum('POD_CHARGE')  # pod_rows' rows, by name
PAPER, INK = O['LIGHT_PAPER'], O['LIGHT_INK']
REC_NAME, REC_PATH, REC_CUE_START = O['REC_NAME'], O['REC_PATH'], O['REC_CUE_START']
KEY_PLAY, KEY_RETURN, KEY_CENTER = O['KEY_PLAY'], O['KEY_RETURN'], O['KEY_CENTER']
CP1252 = {0x80: 0x20ac, 0x82: 0x201a, 0x83: 0x192, 0x84: 0x201e, 0x85: 0x2026, 0x86: 0x2020, 0x87: 0x2021, 0x88: 0x2c6,
          0x89: 0x2030, 0x8a: 0x160, 0x8b: 0x2039, 0x8c: 0x152, 0x8e: 0x17d, 0x91: 0x2018, 0x92: 0x2019, 0x93: 0x201c,
          0x94: 0x201d, 0x95: 0x2022, 0x96: 0x2013, 0x97: 0x2014, 0x98: 0x2dc, 0x99: 0x2122, 0x9a: 0x161, 0x9b: 0x203a,
          0x9c: 0x153, 0x9e: 0x17e, 0x9f: 0x178}
ACCENTS = [[int(v, 16) for v in re.findall(r'0x[0-9a-f]+', row)]
           for row in re.findall(r'\{ (0x[^}]*)\}', (host.ROOT/'patch/offsets.inc').read_text())][:9]


LOWER = bytes.maketrans(bytes(range(65, 91)), bytes(range(97, 123)))


class Heap:
    """Exact-size blocks from the C's own allocator: AddressSanitizer can't see past a ctypes buffer."""
    def __init__(self, fw):
        self.calloc = fw.fn('calloc', C.c_void_p, C.c_size_t, C.c_size_t)
        self.free = fw.fn('free', None, C.c_void_p)

    def string(self, data, room=None):
        block = self.calloc(len(data) + 1 if room is None else room, 1)
        C.memmove(block, data, len(data))
        return block


@pytest.fixture
def heap(fw):
    return Heap(fw)


class Config:
    """config.ini: a key's value, or the default; a missing key with an empty default leaves out alone."""
    def __init__(self, fw, **values):
        self.values = {k.encode(): v for k, v in values.items()}
        fw.mock('toolsReadConfig', self.read)

    def read(self, path, section, key, out, default, *_):
        value = self.values.get(C.string_at(key), C.string_at(default))
        if value or C.string_at(key) in self.values:
            C.memmove(out, value + b'\0', len(value) + 1)


def strings(fw):
    """Stock's AWTK string helpers, which the C leans on."""
    fw.mock('tk_str_start_with', lambda s, prefix, *_: C.string_at(s).startswith(C.string_at(prefix)))


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


def stock_stub(address, result, log):
    """x86-64 at a fixed stock address: its first three arguments logged, result returned. Only ever in a fork."""
    claim(address)
    code = (b'\x48\xb8' + struct.pack('<Q', C.addressof(log)) + bytes.fromhex('488938 48897008 48895010') +
            b'\xb8' + struct.pack('<I', result) + b'\xc3')
    C.memmove(address, code, len(code))


def rgba(c, a=0xff):
    return a << 24 | (c & 255) << 16 | c & 0xff00 | c >> 16


def channels(color):
    return color & 255, color >> 8 & 255, color >> 16 & 255


# The clock.
@pytest.mark.parametrize('wall, back', [(0, 0), (1700000000, 1700000000), (0x7fffffff, 0x7fffffff),
                                        (0x80000000, 0x80000000 - 883612800)])
def test_clock_back_leaves_32_bit_times(fw, wall, back):
    assert fw.fn('clock_back', C.c_long, C.c_longlong)(wall) == back


def test_clock_back_keeps_the_calendar_until_2100(fw):
    clock_back = fw.fn('clock_back', C.c_long, C.c_longlong)
    utc, rng = datetime.timezone.utc, random.Random(2038)
    end = int(datetime.datetime(2100, 1, 1, tzinfo=utc).timestamp())
    for wall in [0x80000000, end - 1] + [rng.randrange(0x80000000, end) for _ in range(500)]:
        back = clock_back(wall)
        assert 0 <= back <= 0x7fffffff
        a, b = (datetime.datetime.fromtimestamp(t, utc) for t in (wall, back))
        assert (a.month, a.day, a.hour, a.minute, a.second, a.weekday()) == (b.month, b.day, b.hour, b.minute, b.second, b.weekday())


@pytest.mark.parametrize('hour, day', [(0, 0), (6, 0), (7, 1), (12, 1), (18, 1), (19, 0), (23, 0)])
def test_light_day_is_seven_to_nineteen(fw, hour, day):
    tm = (C.c_int * 11)(0, 0, hour)
    assert fw.fn('light_day', C.c_int, C.POINTER(C.c_int))(tm) == day


def test_no_clock_is_no_light_day(fw):
    assert fw.fn('light_day', C.c_int, C.POINTER(C.c_int))(None) == 0


@pytest.fixture
def clock(fw):
    """time and localtime_r: hour is what the clock says, None that it can't be read."""
    state = {'hour': 12}
    def local(now, out, *_):
        C.c_int.from_address(out + 8).value = state['hour']
        return out
    fw.mock('fw_time', lambda *_: -1 if state['hour'] is None else 1700000000)
    fw.mock('fw_localtime_r', local)
    return state


@pytest.mark.parametrize('hour, night', [(20, 1), (3, 1), (12, 0), (7, 0), (19, 1), (None, 0)])
def test_clock_night_is_when_lights_are_dark(fw, clock, hour, night):
    clock['hour'] = hour
    assert fw.fn('clock_night', C.c_int)() == night


def test_time_zones_list_west_to_east_ties_in_their_order(fw):
    zones, order = host.tools_build.TIMEZONES, host.tools_build.TIMEZONE_ORDER
    names = [zone.encode() for _, zone in zones]
    rng = random.Random(0x7a)
    offset = {name: rng.randrange(-12, 15) * 3600 for name in names}
    now = {}
    fw.mock('toolsSetTimeZone', lambda name, *_: now.__setitem__('zone', C.string_at(name)))
    def local(when, out, *_):
        C.c_int.from_address(out + 36).value = offset[now['zone']]
        return out
    fw.mock('fw_localtime_r', local)
    rows = (C.c_ubyte * len(zones))()
    fw.fn('navigation__tz_now', None, C.c_void_p)(rows)
    assert list(rows) == sorted(order, key=lambda z: offset[names[z]])
    assert now['zone'] == names[fw.var('g_time_zone', C.c_ubyte).value]  # the saved zone is set again


# Light Mode.
@pytest.mark.parametrize('color, neutral', [
    (0x000000, 1), (0xffffff, 1), (0x808080, 1), (0xff808080, 1), (0x00808080, 1),
    (0x102010, 1), (0x102110, 0), (0x201020, 1), (0x211021, 0), (0xff0000, 0), (0x00ff00, 0), (0x0000ff, 0),
])
def test_neutral_is_a_spread_of_16(fw, color, neutral):
    assert fw.fn('neutral', C.c_int, C.c_uint)(color) == neutral


@pytest.mark.parametrize('value, flipped', [(0, PAPER), (255, INK), (128, PAPER - 128 * (PAPER - INK) // 255)])
def test_flip_v_runs_paper_to_ink(fw, value, flipped):
    assert fw.fn('flip_v', C.c_uint, C.c_uint, C.c_uint, C.c_uint)(value, PAPER, INK) == flipped


def test_flip_v_only_falls(fw):
    flip_v = fw.fn('flip_v', C.c_uint, C.c_uint, C.c_uint, C.c_uint)
    values = [flip_v(v, PAPER, INK) for v in range(256)]
    assert values == sorted(values, reverse=True) and min(values) == INK and max(values) == PAPER


@pytest.mark.parametrize('color, flipped', [
    (0xff000000, 0xff000000 | PAPER * 0x010101),
    (0xffffffff, 0xff000000 | INK * 0x010101),
    (0x80000000, 0x80000000 | PAPER * 0x010101),  # alpha stays
    (0xff4814ff, 0xff4814ff),                     # a hue stays
    (0x00123456, 0x00123456),
])
def test_flip(fw, color, flipped):
    assert fw.fn('flip', C.c_uint, C.c_uint)(color) == flipped


def test_flip_is_per_channel_on_a_slightly_coloured_grey(fw):
    flip_v = fw.fn('flip_v', C.c_uint, C.c_uint, C.c_uint, C.c_uint)
    assert fw.fn('flip', C.c_uint, C.c_uint)(0xff102010) == \
        0xff000000 | flip_v(0x10, PAPER, INK) << 16 | flip_v(0x20, PAPER, INK) << 8 | flip_v(0x10, PAPER, INK)


def test_flip_if(fw):
    flip_if = fw.fn('flip_if', C.c_uint, C.c_int, C.c_uint)
    assert flip_if(0, 0xff000000) == 0xff000000 and flip_if(1, 0xff000000) == 0xff000000 | PAPER * 0x010101


@pytest.mark.parametrize('light, color, inked', [
    (b'0', 0xff000000, 0xff000000), (b'1', 0xff000000, 0xff000000 | PAPER * 0x010101),
    (b'1', 0x00000000, 0x00000000),  # clear stays clear: stock tests for it
])
def test_ui_ink_flips_only_in_light_mode(fw, clock, light, color, inked):
    Config(fw, LIGHT=light)
    assert fw.fn('ui_ink', C.c_uint, C.c_uint)(color) == inked


@pytest.mark.parametrize('hour, light', [(12, 1), (22, 0)])
def test_light_auto_follows_the_clock(fw, clock, hour, light):
    clock['hour'] = hour
    Config(fw, LIGHT=b'2')
    assert fw.fn('ipod_light', C.c_int)() == light


# The accent's red.
def red_map(fw):
    return fw.fn('red_map', C.c_uint, C.c_uint, C.c_uint, C.c_uint, C.c_int)


def close(color, want, slack=3):
    return all(abs(a - b) <= slack for a, b in zip(channels(color), want))


def test_red_maps_to_the_tone_exactly(fw):
    assert red_map(fw)(rgba(STOCK_RED), 0x1e90ff, 0xffffff, 0) == rgba(0x1e90ff)


def test_red_map_keeps_alpha(fw):
    assert red_map(fw)(rgba(STOCK_RED, 0x40), 0x1e90ff, 0xffffff, 0) == rgba(0x1e90ff, 0x40)


@pytest.mark.parametrize('grey', [0, 0x20, 0x80, 0xc0, 0xff])
def test_greys_stay(fw, grey):
    assert red_map(fw)(rgba(grey * 0x010101), 0x1e90ff, 0xffffff, 0) == rgba(grey * 0x010101)


@pytest.mark.parametrize('hue', [0x0000ff, 0x00ff00, 0x00ffff, 0xffff00, 0xff00ff, 0x804000, 0x123456])
def test_other_hues_stay(fw, hue):
    assert red_map(fw)(rgba(hue), 0x1e90ff, 0xffffff, 0) == rgba(hue)


BLENDS = [(1, 0), (.75, 0), (.5, 0), (.25, 0), (.75, .25), (.5, .5), (.25, .75)]


def blended(t, k, tone, white=255):
    return [t * (tone >> s & 255) + k * white for s in (16, 8, 0)]


@pytest.mark.parametrize('t, k', BLENDS)
def test_a_blend_keeps_its_share_of_the_tone(fw, t, k):
    c = sum(round(v) << 8 * i for i, v in enumerate(blended(t, k, STOCK_RED))) | 0xff000000
    assert close(red_map(fw)(c, 0x2a6ad8, 0xffffff, 0), blended(t, k, 0x2a6ad8))


@pytest.mark.parametrize('t, k', BLENDS)
def test_crimson_is_the_identity(fw, t, k):
    c = sum(round(v) << 8 * i for i, v in enumerate(blended(t, k, STOCK_RED))) | 0xff000000
    assert close(red_map(fw)(c, STOCK_RED, 0xffffff, 0), channels(c))


@pytest.mark.parametrize('glyph, want', [(0x000000, (0, 0, 0)), (0x123456, (0x12, 0x34, 0x56)), (0xff0000, (255, 0, 0))])
def test_white_becomes_the_glyph(fw, glyph, want):
    assert channels(red_map(fw)(rgba(0xffffff), 0x1e90ff, glyph, 0)) == want


def test_a_grey_takes_its_share_of_the_glyph(fw):
    assert channels(red_map(fw)(rgba(0x808080), 0x1e90ff, 0xff0000, 0)) == (128, 0, 0)


def test_light_red_is_the_tone(fw):
    assert red_map(fw)(rgba(STOCK_RED), 0x2a6ad8, 0xffffff, 1) == rgba(0x2a6ad8)


@pytest.mark.parametrize('color, value', [(0x000000, PAPER), (0xffffff, INK)])
def test_light_flips_the_neutral_part(fw, color, value):
    assert red_map(fw)(rgba(color), 0x2a6ad8, 0xffffff, 1) == rgba(value * 0x010101)


@pytest.mark.parametrize('t', [.75, .5, .25])
def test_light_puts_a_tint_over_paper(fw, t):
    tint = sum(round(v) << 8 * i for i, v in enumerate(blended(t, 0, STOCK_RED))) | 0xff000000
    want = [t * (0x2a6ad8 >> s & 255) + (1 - t) * PAPER for s in (16, 8, 0)]
    assert close(red_map(fw)(tint, 0x2a6ad8, 0xffffff, 1), want)


def test_red_map_any_colour(fw):
    mapped, rng = red_map(fw), random.Random(0x4ed)
    for _ in range(3000):
        c, tone, glyph = rng.getrandbits(32), rng.getrandbits(24), rng.choice((0xffffff, 0, rng.getrandbits(24)))
        out = mapped(c, tone, glyph, rng.randrange(2))
        assert out >> 24 == c >> 24


def test_red_map_every_tone_of_the_extremes(fw):
    mapped = red_map(fw)
    for r in range(0, 256, 15):
        for g in range(0, 256, 15):
            for b in range(0, 256, 15):
                assert mapped(rgba(r << 16 | g << 8 | b), 0xffffff, 0xffffff, 0) >> 24 == 0xff


def test_accent_map_crimson_keeps_every_colour(fw, clock):
    Config(fw)
    accent_map = fw.fn('accent_map', C.c_uint, C.c_uint, C.c_int, C.c_int)
    assert accent_map(rgba(0x7f0a24), O['CRIMSON'], 3) == rgba(0x7f0a24)


def test_accent_map_maps_red_to_the_presets_tone(fw, clock):
    Config(fw)
    accent_map = fw.fn('accent_map', C.c_uint, C.c_uint, C.c_int, C.c_int)
    assert accent_map(rgba(STOCK_RED), 4, 3) == rgba(ACCENTS[4][3])


def test_accent_map_in_light_mode_flips_neutrals_and_keeps_clear(fw, clock):
    Config(fw, LIGHT=b'1')
    accent_map = fw.fn('accent_map', C.c_uint, C.c_uint, C.c_int, C.c_int)
    assert accent_map(rgba(0xffffff), 4, 3) == rgba(INK * 0x010101)
    assert accent_map(rgba(STOCK_RED, 0), 4, 3) == rgba(STOCK_RED, 0)


def test_a_neutral_accent_in_light_mode_takes_its_silver_for_both(fw, clock):
    Config(fw, LIGHT=b'1', THEME=b'2', ACCENT=b'0')
    tone = fw.fn('accent_tone', C.c_uint, C.c_int)
    assert tone(0) == tone(2) == ACCENTS[0][3]


def test_a_coloured_accent_keeps_its_tones(fw, clock):
    Config(fw, THEME=b'2', ACCENT=b'4')
    tone = fw.fn('accent_tone', C.c_uint, C.c_int)
    assert [tone(i) for i in range(5)] == ACCENTS[4]


def test_ivory_is_minimal_only(fw, clock):
    Config(fw, THEME=b'2', ACCENT=b'7')
    tone = fw.fn('accent_tone', C.c_uint, C.c_int)
    assert tone(0) == ACCENTS[0][0]  # Classic has no Ivory: Graphite


def test_ivory_on_minimal(fw, clock):
    Config(fw, MINIMAL_ACCENT=b'7')  # Minimal's Ivory
    assert fw.fn('accent_tone', C.c_uint, C.c_int)(2) == O['MINIMAL_FILL']


def test_mix_runs_from_one_to_the_other(fw):
    mix = fw.fn('mix', C.c_uint, C.c_uint, C.c_uint, C.c_int, C.c_int)
    assert mix(0x102030, 0xf0e0d0, 0, 4) == 0x102030 and mix(0x102030, 0xf0e0d0, 4, 4) == 0xf0e0d0
    assert mix(0x000000, 0xff00ff, 1, 2) == 0x7f007f


# Settings in config.ini.
@pytest.mark.parametrize('value, n, default, want', [
    (b'0', 3, 1, 0), (b'2', 3, 1, 2), (b'3', 3, 1, 1), (b'', 3, 1, 1), (b'22', 3, 2, 2), (b'x', 2, 0, 0),
    (b'1 ', 2, 0, 0), (b'-1', 3, 2, 2), (b'/', 3, 2, 2), (b'8', 9, 7, 8), (b'9', 9, 7, 7),
])
def test_config_value(fw, value, n, default, want):
    Config(fw, K=value)
    assert fw.fn('config_value', C.c_int, C.c_char_p, C.c_char_p, C.c_int, C.c_int)(b'S', b'K', n, default) == want


def test_config_value_of_a_missing_key_is_the_default(fw):
    Config(fw)
    assert fw.fn('config_value', C.c_int, C.c_char_p, C.c_char_p, C.c_int, C.c_int)(b'S', b'K', 3, 2) == 2


def test_config_value_any_text(fw):
    value = fw.fn('config_value', C.c_int, C.c_char_p, C.c_char_p, C.c_int, C.c_int)
    config, rng = Config(fw), random.Random(0xc0f)
    for _ in range(2000):
        raw = bytes(rng.randrange(1, 256) for _ in range(rng.randrange(0, 200)))
        config.values[b'K'] = raw
        n, default = rng.randrange(1, 10), rng.randrange(0, 8)
        want = raw[0] - 48 if len(raw) == 1 and 48 <= raw[0] < 48 + n else default
        assert value(b'S', b'K', n, default) == want


@pytest.mark.parametrize('i, choices', [(0, 2), (1, 2), (2, 3), (3, 2), (4, 2), (10, 4), (11, 2), (12, 2), (15, 2), (POD['POD_BIG_LIB'], 2), (POD['POD_PL_CF'], 2)])
def test_pod_rows_choices(fw, i, choices):
    assert fw.fn('navigation__pod_choices', C.c_int, C.c_uint)(i) == choices


def test_pod_settings_defaults(fw):
    Config(fw)
    assert fw.fn('ringnav_artist_view', C.c_int)() == 1  # Artist albums defaults to Coverflow
    assert [fw.fn(name, C.c_int)() for name in ('ringnav_year_old', 'ringnav_artist_az',
                                                'ringnav_lock_clock', 'ringnav_bt_resume',
                                                'ringnav_big_library', 'ringnav_playlist_coverflow')] == [0] * 6  # Fast Navigation off


def test_pod_settings_read_their_keys_once(fw):
    Config(fw, YEAROLD=b'1', ARTISTCF=b'2', PLAYLISTCF=b'1', ARTISTAZ=b'9', VOLGUARD=b'1', BIGLIBRARY=b'1')
    assert fw.fn('ringnav_artist_view', C.c_int)() == 2 and fw.fn('ringnav_big_library', C.c_int)() == 1
    assert fw.fn('ringnav_playlist_coverflow', C.c_int)() == 1
    assert fw.fn('ringnav_year_old', C.c_int)() == 1 and fw.fn('ringnav_artist_az', C.c_int)() == 0
    assert fw.var('wheel_guard', C.c_int).value == 1
    count = len(fw.calls('toolsReadConfig'))
    fw.fn('ringnav_year_old', C.c_int)()
    assert len(fw.calls('toolsReadConfig')) == count


def test_artists_default_to_album_artist(fw):
    config = Config(fw)
    fw.fn('ringnav_read_artistype', None)()
    assert fw.var('fw_ctx_artist_type', C.c_int).value == 1
    config.values[b'ARTISTTYPE'] = b'0'
    fw.fn('ringnav_read_artistype', None)()
    assert fw.var('fw_ctx_artist_type', C.c_int).value == 0


@pytest.mark.parametrize('values, theme, home, light', [
    ({}, 0, 0, 0), (dict(THEME=b'2', HOME=b'2', LIGHT=b'1'), 2, 2, 1), (dict(THEME=b'7', HOME=b'3', LIGHT=b'3'), 0, 0, 0),
])
def test_display_settings(fw, clock, st, values, theme, home, light):
    Config(fw, **values)
    assert (fw.fn('ipod_home', C.c_int)(), fw.fn('ipod_light', C.c_int)(), st('theme').value) == (home, light, theme)


@pytest.mark.parametrize('value, rockbox, name', [
    (b'', 1, b'Rockbox'), (b'', 0, b'Favorites'), (b'0', 0, b'Rockbox'), (b'1', 0, b'Artists'), (b'2', 0, b'Albums'),
    (b'3', 0, b'Favorites'),
    (b'4', 0, b'Rated Albums'), (b'5', 0, b'Shuffle'), (b'6', 0, b'Games'), (b'7', 0, b'Playlists'), (b'8', 0, b'Podcasts'),
    (b'9', 0, b'Audiobooks'), (b'10', 0, b'Groovebox'), (b'11', 1, b'Rockbox'), (b'11', 0, b'Favorites'),  # None: the carousel's card takes the default
    (b'12', 1, b'Rockbox'), (b'12', 0, b'Favorites'), (b'1x', 0, b'Favorites'),
])
def test_shortcut(fw, clock, value, rockbox, name):
    Config(fw, **({'SHORTCUT': value} if value else {}))
    def stat(version, path, info, *_):
        struct.pack_into('<I', (C.c_char * 144).from_address(info), 20, 0o100755)
        return 0 if rockbox and C.string_at(path) == b'/mnt/mmc/.rockbox/rockbox' else -1
    fw.mock('fw___xstat', stat)
    assert fw.fn('ipod_home_shortcut', C.c_char_p)() == name


@pytest.mark.parametrize('value, rockbox, groovebox, shown', [
    (b'10', 1, 0, 0), (b'10', 0, 1, 1), (b'0', 0, 1, 0), (b'0', 1, 0, 1), (b'3', 0, 0, 1),
    (b'11', 1, 1, 0), (b'11', 0, 0, 0), (b'12', 1, 0, 1), (b'12', 0, 0, 1),  # None hides it, an unknown value is the default
])
def test_the_shortcut_row_hides_without_its_launcher(fw, clock, value, rockbox, groovebox, shown):
    Config(fw, SHORTCUT=value)
    there = {b'/mnt/mmc/.rockbox/rockbox': rockbox, b'/mnt/mmc/.groovebox/q2-groovebox': groovebox}
    def stat(version, path, info, *_):
        struct.pack_into('<I', (C.c_char * 144).from_address(info), 20, 0o100755)
        return 0 if there.get(C.string_at(path)) else -1
    fw.mock('fw___xstat', stat)
    assert fw.fn('ipod_home_rockbox', C.c_int)() == shown


@pytest.mark.parametrize('fn, path', [('rockbox_available', b'/mnt/mmc/.rockbox/rockbox'),
                                      ('groovebox_available', b'/mnt/mmc/.groovebox/q2-groovebox')])
@pytest.mark.parametrize('result, mode, available', [(0, 0o100755, 1), (0, 0o040755, 0), (-1, 0o100755, 0)])
def test_rockbox_is_a_file_on_the_card(fw, fn, path, result, mode, available):
    def stat(version, p, info, *_):
        assert version == 3 and C.string_at(p) == path
        C.c_uint.from_address(info + 20).value = mode
        return result
    fw.mock('fw___xstat', stat)
    assert fw.fn(fn, C.c_int)() == available


@pytest.mark.parametrize('flag, mode, choice', [(0, 1, 0), (1, 3, 1), (1, 2, 2), (1, 1, 3)])
def test_lock_choice_names_stocks_modes(fw, flag, mode, choice):
    fw.var('g_keylock_flag', C.c_ubyte).value, fw.var('g_keylock_mode', C.c_ubyte).value = flag, mode
    assert fw.fn('navigation__lock_choice', C.c_int)() == choice and not fw.calls('write_int_config')


@pytest.mark.parametrize('mode', [0, 4, 200])
def test_an_unknown_lock_mode_locks_everything(fw, mode):
    fw.var('g_keylock_flag', C.c_ubyte).value, fw.var('g_keylock_mode', C.c_ubyte).value = 1, mode
    assert fw.fn('navigation__lock_choice', C.c_int)() == 1
    assert fw.var('g_keylock_mode', C.c_ubyte).value == 3 and len(fw.calls('write_int_config')) == 1


@pytest.mark.parametrize('choice', range(4))
def test_a_lock_choice_comes_back(fw, choice):
    fw.fn('navigation__lock_set', None, C.c_int)(choice)
    assert fw.fn('navigation__lock_choice', C.c_int)() == choice
    assert [c[1][0] for c in fw.calls('write_int_config')] == [fw.var('g_keylock_mode', C.c_ubyte).value, int(choice > 0)]


@pytest.mark.parametrize('saved, volume, top, want', [
    (b'50', 20, 100, 50), (b'0', 20, 100, 0), (b'100', 20, 100, 100), (b'101', 20, 100, 20), (b'', 20, 100, 20),
    (b'abc', 20, 100, 20), (b'1000', 20, 100, 20), (b'5x', 20, 100, 20), (b'90', 20, 80, 80),
])
def test_a_new_output_takes_its_saved_volume(fw, saved, volume, top, want):
    Config(fw, BT_VOLUME=saved)
    fw.var('g_volume', C.c_ubyte).value, fw.var('g_maxvolume', C.c_ubyte).value = volume, top
    fw.mock('mclGetOutputWay', lambda *_: 0)
    fw.fn('ringnav_output', C.c_int, C.c_int)(1)
    assert fw.var('g_volume', C.c_ubyte).value == want and fw.calls('device_set_volume')[-1][1][0] == want


def test_a_saved_volume_any_text(fw):
    config = Config(fw)
    fw.mock('mclGetOutputWay', lambda *_: 0)
    fw.var('g_maxvolume', C.c_ubyte).value = 100
    output = fw.fn('ringnav_output', C.c_int, C.c_int)
    for data in hostile(random.Random(0x701), [b'50', b'100', b'101', b'007', b'1000', b'5x'], 1500, 240):
        config.values[b'BT_VOLUME'] = data
        fw.var('g_volume', C.c_ubyte).value = 20
        output(1)
        good = re.fullmatch(rb'\d{1,3}', data) and int(data) <= 100
        assert fw.var('g_volume', C.c_ubyte).value == (int(data) if good else 20)


def test_a_same_kind_output_leaves_the_volume(fw):
    fw.var('g_volume', C.c_ubyte).value = 33
    fw.mock('mclGetOutputWay', lambda *_: 0)
    fw.fn('ringnav_output', C.c_int, C.c_int)(2)
    assert fw.var('g_volume', C.c_ubyte).value == 33 and not fw.calls('device_set_volume')


@pytest.mark.parametrize('way, section, key, saved', [(1, b'PLAYSET', b'VOLUME', b'BT_VOLUME'), (0, b'PLAYSET', b'VOLUME', b'VOLUME'),
                                                      (1, b'OTHER', b'VOLUME', b'VOLUME'), (1, b'PLAYSET', b'BRIGHT', b'BRIGHT')])
def test_bluetooth_keeps_its_own_volume(fw, way, section, key, saved):
    strings(fw)
    seen = []
    fw.mock('mclGetOutputWay', lambda *_: way)
    fw.mock('stock_write_config_trampoline', lambda value, s, k, *_: seen.append(C.string_at(k)) and 0)
    fw.fn('ringnav_write_config', C.c_int, C.c_int, C.c_char_p, C.c_char_p)(5, section, key)
    assert seen == [saved]


# Folder Root.
class Card:
    def __init__(self, fw, entries):
        self.entries = [struct.pack('<IIHB256s', 0, 0, 0, kind, name) for name, kind in entries]
        self.buffer = C.create_string_buffer(300)
        self.at = 0
        fw.mock('fw_opendir', lambda *_: 7)
        fw.mock('fw_readdir', self.read)

    def read(self, *_):
        if self.at == len(self.entries):
            return 0
        C.memmove(self.buffer, self.entries[self.at], len(self.entries[self.at]))
        self.at += 1
        return C.addressof(self.buffer)


def root_next(fw, entries, name):
    Card(fw, entries)
    out = C.create_string_buffer(name, 256)
    fw.fn('navigation__root_next', None, C.c_void_p)(out)
    return out.value


FOLDERS = [(b'Music', 4), (b'.hidden', 4), (b'art', 0), (b' edge', 4), (b'edge ', 4), (b'file.txt', 8), (b'Zed', 4), (b'x' * 201, 4), (b'y' * 200, 4)]


@pytest.mark.parametrize('name, following', [(b'', b'art'), (b'art', b'Music'), (b'MUSIC', b'y' * 200), (b'y' * 200, b'Zed'), (b'zed', b''), (b'Gone', b'Music')])
def test_folder_root_steps_through_top_folders(fw, name, following):
    assert root_next(fw, FOLDERS, name) == following


def test_folder_root_any_names(fw):
    next_ = fw.fn('navigation__root_next', None, C.c_void_p)
    rng = random.Random(0xf01)
    card = Card(fw, [])
    for _ in range(600):
        entries = [(bytes(rng.randrange(1, 256) for _ in range(rng.randrange(1, 255))), rng.choice((0, 4, 4, 8))) for _ in range(6)]
        card.entries = [struct.pack('<IIHB256s', 0, 0, 0, kind, name) for name, kind in entries]
        card.at = 0
        out = C.create_string_buffer(rng.choice((b'', b'm', entries[0][0])), 256)
        next_(out)
        assert len(out.value) <= 200 and out.raw[255] == 0


def test_no_card_is_the_card(fw):
    out = C.create_string_buffer(b'Music', 256)
    fw.fn('navigation__root_next', None, C.c_void_p)(out)
    assert out.value == b''


@pytest.mark.parametrize('name, mode, stat, opens', [
    (b'Music', 0o040755, 0, 1), (b'', 0o040755, 0, 0), (b'.hidden', 0o040755, 0, 0), (b'a/b', 0o040755, 0, 0),
    (b'Music', 0o100644, 0, 0), (b'Music', 0o040755, -1, 0),
])
def test_folder_root_opens_only_a_top_level_folder(fw, name, mode, stat, opens):
    Config(fw, FOLDERROOT=name)
    def xstat(version, path, info, *_):
        C.c_uint.from_address(info + 20).value = mode
        return stat
    fw.mock('fw___xstat', xstat)
    path = C.create_string_buffer(265)
    assert fw.fn('navigation__root_dir', C.c_int, C.c_void_p)(path) == opens
    assert path.value == b'/mnt/mmc/' + name


def test_folder_root_any_saved_name(fw):
    config = Config(fw)
    fw.mock('fw___xstat', lambda version, path, info, *_: C.c_uint.from_address(info + 20).__setattr__('value', 0o040755))
    root_dir = fw.fn('navigation__root_dir', C.c_int, C.c_void_p)
    rng = random.Random(0xf02)
    for _ in range(1200):
        name = bytes(rng.choice(b'ab./ \xe9') for _ in range(rng.randrange(0, 255)))
        config.values[b'FOLDERROOT'] = name
        path = C.create_string_buffer(265)
        assert root_dir(path) == (bool(name) and name[:1] != b'.' and b'/' not in name)


def test_folder_root_of_the_longest_name_fits(fw):
    name = b'n' * 255
    Config(fw, FOLDERROOT=name)
    path = C.create_string_buffer(265)
    fw.fn('navigation__root_dir', C.c_int, C.c_void_p)(path)
    assert len(path.value) == 264 and path.raw[264] == 0


# Comma lists.
@pytest.mark.parametrize('items, item, tail', [
    (b'a,bc,d', b'bc', b'bc,d'), (b'a,bc,d', b'b', None), (b'a,bc,d', b'd', b'd'), (b'a,bc,d', b'a', b'a,bc,d'), (b'', b'a', None),
    (b'a', b'', None), (b'', b'', None), (b'a,,b', b'', None), (b'a,,b', b'b', b'b'), (b',a', b'a', b'a'), (b'a,', b'a', b'a,'),
    (b'ab', b'a', None), (b'ab', b'b', None), (b'abc,bc', b'bc', b'bc'),
])
def test_csv_find(fw, items, item, tail):
    assert fw.fn('csv_find', C.c_char_p, C.c_char_p, C.c_char_p)(items, item) == tail


@pytest.mark.parametrize('before, size, item, result, after', [
    (b'', 2, b'a', 1, b'a'), (b'', 1, b'a', -1, b''),                 # exact fit, one short
    (b'a', 4, b'b', 1, b'a,b'), (b'a', 3, b'b', -1, b'a'),
    (b'a,b', 8, b'b', 0, b'a'), (b'a,b', 8, b'a', 0, b'b'), (b'a', 8, b'a', 0, b''), (b'a,b,c', 8, b'b', 0, b'a,c'),
    (b'ab,a', 8, b'a', 0, b'ab'), (b'a,b', 8, b'', 0, b'a,b'),
    (b'a,a', 8, b'a', 0, b'a'),                                       # the first of two
    (b'abc', 4, b'abc', 0, b''),                                      # taking out needs no room
])
def test_csv_toggle(fw, heap, before, size, item, result, after):
    items = heap.string(before, size)
    assert fw.fn('csv_toggle', C.c_int, C.c_void_p, C.c_uint, C.c_char_p)(items, size, item) == result
    assert C.string_at(items) == after


def test_csv_any_list_against_a_model(fw, heap):
    toggle = fw.fn('csv_toggle', C.c_int, C.c_void_p, C.c_uint, C.c_void_p)
    find = fw.fn('csv_find', C.c_void_p, C.c_void_p, C.c_void_p)
    rng = random.Random(0xc5f)
    for _ in range(3000):
        size = rng.randrange(1, 48)
        body = bytes(rng.choice(b'ab,,\xff') for _ in range(rng.randrange(size)))
        item = bytes(rng.choice(b'ab\xff') for _ in range(rng.randrange(4)))
        if rng.random() < .1:
            item += b','
        items, needle = heap.string(body, size), heap.string(item)
        entries = body.split(b',') if body else []
        at = entries.index(item) if item and item in entries else -1
        assert (find(items, needle) is not None) == (at >= 0)
        got = toggle(items, size, needle)
        if not item:
            assert got == 0 and C.string_at(items) == body
        elif at >= 0:
            assert got == 0 and C.string_at(items) == b','.join(entries[:at] + entries[at + 1:])
        elif len(body) + (len(body) > 0) + len(item) >= size:
            assert got == -1 and C.string_at(items) == body
        else:
            assert got == 1 and C.string_at(items) == (body + b',' + item if body else item)
        heap.free(items), heap.free(needle)


def test_csv_toggle_round_trip(fw, heap):
    toggle = fw.fn('csv_toggle', C.c_int, C.c_void_p, C.c_uint, C.c_char_p)
    items = heap.string(b'', 32)
    for key in (b'TIDAL', b'deezer', b'RADIO'):
        assert toggle(items, 32, key) == 1
    assert C.string_at(items) == b'TIDAL,deezer,RADIO'
    for key in (b'deezer', b'RADIO', b'TIDAL'):
        assert toggle(items, 32, key) == 0
    assert C.string_at(items) == b''


# Streaming's services.
@pytest.mark.parametrize('i, key, label', [(0, b'TIDAL', b'TIDAL'), (1, b'SPOTIFY', b'Spotify'), (2, b'DOWNLOADS', b'Tidal Downloads'),
                                           (3, b'RADIO', b'Internet Radio'), (4, b'AIRPLAY', b'AirPlay')])
def test_fixed_services(fw, i, key, label):
    svc = fw.fn('navigation__svc', C.c_char_p, C.c_int, C.c_int)
    assert svc(i, 0) == key and svc(i, 1) == label


def test_card_apps_sit_between_spotify_and_the_rest(fw, st):
    svc = fw.fn('navigation__svc', C.c_char_p, C.c_int, C.c_int)
    st('svc_n').value = 2
    assert svc(1, 0) == b'SPOTIFY' and svc(2, 0) is None and svc(3, 0) is None  # no apps on this card
    assert svc(4, 0) == b'DOWNLOADS' and svc(6, 0) == b'AIRPLAY'


def test_svc_find(fw, st):
    find = fw.fn('svc_find', C.c_char_p, C.c_char_p, C.c_int)
    assert find(b'TIDAL,RADIO', 3) == b'RADIO' and find(b'TIDAL,RADIO', 1) is None
    st('svc_n').value = 1
    assert find(b'TIDAL', 2) is None  # an app gone from the card has no key


# Tag text.
def utf8(data):
    try:
        data.decode()
        return True
    except UnicodeDecodeError:
        return False


@pytest.mark.parametrize('data, valid', [
    (b'', 1), (b'ascii', 1), ('Café'.encode(), 1), ('音乐'.encode(), 1), ('𝄞'.encode(), 1), (b'\xc2\x80', 1), (b'\xdf\xbf', 1),
    (b'\xe0\xa0\x80', 1), (b'\xed\x9f\xbf', 1), (b'\xee\x80\x80', 1), (b'\xf0\x90\x80\x80', 1), (b'\xf4\x8f\xbf\xbf', 1),
    (b'\x80', 0), (b'\xbf', 0), (b'\xc0\x80', 0), (b'\xc1\xbf', 0),            # no lead, overlong
    (b'\xe0\x9f\xbf', 0), (b'\xed\xa0\x80', 0), (b'\xed\xbf\xbf', 0),          # overlong, surrogates
    (b'\xf0\x8f\xbf\xbf', 0), (b'\xf4\x90\x80\x80', 0), (b'\xf5\x80\x80\x80', 0), (b'\xff', 0), (b'\xfe', 0),
    (b'\xc3', 0), (b'\xe9\x9f', 0), (b'\xf0\x9f\x8e', 0), (b'caf\xe9', 0), (b'\xe9t\xe9', 0),  # truncated
])
def test_utf8_text(fw, heap, data, valid):
    assert fw.fn('utf8_text', C.c_int, C.c_void_p)(heap.string(data)) == valid


def hostile(rng, seeds, count=2000, limit=64):
    """Random bytes without a NUL, and the seeds cut short and bitten."""
    for _ in range(count):
        kind = rng.randrange(3)
        if kind == 0:
            yield bytes(rng.randrange(1, 256) for _ in range(rng.randrange(limit)))
        else:
            data = bytearray(rng.choice(seeds))
            if kind == 1:
                del data[rng.randrange(len(data) + 1):]
            for _ in range(rng.randrange(3)):
                if data:
                    data[rng.randrange(len(data))] = rng.randrange(1, 256)
            yield bytes(data)


TAGS = ['Café', 'música', '音乐', 'Beyoncé’s', 'ñó', '𝄞x', 'Canção']
SEEDS = [t.encode(enc) for t in TAGS for enc in ('utf-8', 'gb18030') if not t.startswith('𝄞') or enc == 'utf-8'] + \
    [t.encode('cp1252') for t in TAGS if t not in ('音乐', '𝄞x')]


def test_utf8_text_any_bytes_as_python_reads_them(fw, heap):
    check = fw.fn('utf8_text', C.c_int, C.c_void_p)
    for data in hostile(random.Random(0x0f8), SEEDS, 3000):
        block = heap.string(data)
        assert check(block) == utf8(data)
        heap.free(block)


@pytest.mark.parametrize('c', range(256))
def test_ascii_letter(fw, c):
    assert fw.fn('tags__ascii_letter', C.c_int, C.c_uint)(c) == (65 <= c <= 90 or 97 <= c <= 122)


def gbk_like(data):
    letter = lambda i: 0 <= i < len(data) and (65 <= data[i] <= 90 or 97 <= data[i] <= 122)
    i, high = 0, False
    while i < len(data):
        if data[i] < 0x80:
            i += 1
            continue
        trail = data[i + 1] if i + 1 < len(data) else 0
        if data[i] in (0x80, 0xff) or trail < 0x80 or trail == 0xff or (i and letter(i - 1) and letter(i + 2)):
            return False
        high, i = True, i + 2
    return high and not utf8(data)


@pytest.mark.parametrize('data, gbk', [
    ('音乐'.encode('gbk'), 1),                # Chinese text: pairs
    ('周杰伦 - 七里香'.encode('gbk'), 1),
    (b'', 0), (b'plain ascii', 0),
    ('音乐'.encode(), 0),                     # UTF-8 isn't GBK
    ('música'.encode('latin-1'), 0),          # FA 73: a low trail
    ('Lo que la música me enseñó'.encode('latin-1'), 0),
    ('Canção'.encode('latin-1'), 0),          # a pair between letters
    ('Beyoncé’s'.encode('cp1252'), 0),
    ('ñó'.encode('latin-1'), 1),              # a word's edge reads as GBK, as documented
    (b'\xb0', 0), (b'a\xb0', 0),              # no trail
    (b'\x80\xb0', 0), (b'\xff\xb0', 0), (b'\xb0\xff', 0),   # lead and trail limits
    (b'\xb0\x80', 1), (b'\x81\xfe', 1),
    (b'\xb0\xa1 x', 1), (b'x\xb0\xa1', 1), (b'\xb0\xa1x', 1), (b'x \xb0\xa1 y', 1),
    (b'x\xb0\xa1y', 0),
    (b'1\xb0\xa12', 1),                       # digits aren't letters
])
def test_gbk_text(fw, heap, data, gbk):
    assert fw.fn('tags__gbk_text', C.c_int, C.c_void_p)(heap.string(data)) == gbk


def test_gbk_text_any_bytes(fw, heap):
    check = fw.fn('tags__gbk_text', C.c_int, C.c_void_p)
    for data in hostile(random.Random(0x6b), SEEDS, 3000):
        block = heap.string(data)
        assert check(block) == gbk_like(data)
        heap.free(block)


@pytest.mark.parametrize('round_, latin1, keep', [
    (b'abc', b'abd', True),                           # TagLib's bytes changed: its UTF-8 stands
    (b'\xb0\xa1', b'\xb0\xa1', False),                # all the same and GBK: stock's conversion
    (b'm\xfasica', b'm\xfasica', True),               # all the same, Latin-1: not GBK
    (b'ascii', b'ascii', True),
])
def test_gbk_tag(fw, round_, latin1, keep):
    assert bool(fw.fn('ringnav_gbk_tag', C.c_int, C.c_char_p, C.c_char_p)(round_, latin1)) == keep


def test_gbk_tag_any_bytes(fw, heap):
    tag = fw.fn('ringnav_gbk_tag', C.c_int, C.c_void_p, C.c_void_p)
    rng = random.Random(0x7a6)
    for data in hostile(rng, SEEDS, 2000):
        other = data if rng.random() < .8 else data + b'x'
        a, b = heap.string(other), heap.string(data)
        assert bool(tag(a, b)) == (other != data or not gbk_like(data))
        heap.free(a), heap.free(b)


# CUE text: 512 bytes or more at s, fields of 500.
def windows_1252(data):
    out = bytearray()
    for byte in data:
        c = CP1252.get(byte, 63 if 0x80 <= byte < 0xa0 else byte)
        char = chr(c).encode()
        if len(out) + len(char) > 511:
            break
        out += char
    return bytes(out)


def cue_text(fw, heap, data, room=512):
    block = heap.string(data, room)
    assert fw.fn('ringnav_cue_text', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p)(block, None, None) == 1
    return C.string_at(block)


def test_cue_text_of_no_field(fw):
    assert fw.fn('ringnav_cue_text', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p)(None, None, None) == 1


@pytest.mark.parametrize('data', [b'', b'plain', 'Café'.encode(), '音乐'.encode()])
def test_cue_text_keeps_utf8(fw, heap, data):
    assert cue_text(fw, heap, data) == data


@pytest.mark.parametrize('data, want', [
    (b'Caf\xe9', 'Café'), (b'\x93Quote\x94', '“Quote”'), (b'\x80 5', '€ 5'), (b'It\x92s', 'It’s'), (b'na\xefve caf\xe9', 'naïve café'),
    (b'\x81', '?'), (b'\x8d', '?'), (b'\x8f', '?'), (b'\x90', '?'), (b'\x9d', '?'), (b'\xa0', '\xa0'), (b'\xff', 'ÿ'),
])
def test_cue_text_western_is_windows_1252(fw, heap, data, want):
    assert cue_text(fw, heap, data) == want.encode()


@pytest.mark.parametrize('data, length', [
    (b'\xe9' * 500, 510),        # two bytes each: cut at a whole character
    (b'\x80' * 500, 510),        # three each
    (b'a' + b'\xe9' * 255, 511), # exactly the most
    (b'aaa' + b'\xe9' * 254 + b'\x80', 511),  # no room left for the last, three bytes
    (b'ab' + b'\x80' * 170, 509),
])
def test_cue_text_is_cut_at_a_whole_character(fw, heap, data, length):
    out = cue_text(fw, heap, data)
    assert len(out) == length and out == windows_1252(data) and utf8(out)


def test_cue_text_a_long_field_reads_as_windows_1252(fw, heap):
    data = b'\xb0\xa1' * 171  # 342 bytes: GBK by its bytes, past what stock's conversion can hold
    assert cue_text(fw, heap, data) == windows_1252(data)


def test_cue_text_any_field_in_its_512(fw, heap):
    gbk = fw.fn('tags__gbk_text', C.c_int, C.c_void_p)
    rng = random.Random(0xc5e)
    for data in hostile(rng, SEEDS + [b'\xe9' * 500, b'\x80' * 500, b'a' + b'\xe9' * 255], 2500, 500):
        block = heap.string(data, 512)
        if len(data) <= 340 and gbk(block):  # stock's, at a fixed address
            heap.free(block)
            continue
        got = fw.fn('ringnav_cue_text', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p)(block, None, None)
        out = C.string_at(block)
        assert got == 1 and out == (data if utf8(data) else windows_1252(data)) and utf8(out) and len(out) <= 511
        heap.free(block)


@in_fork
@pytest.mark.parametrize('data', [b'\xb0\xa1', b'\xb0\xa1' * 170])
def test_cue_text_gbk_goes_through_stocks_conversion(fw, heap, data):
    log = (C.c_long * 3)()
    stock_stub(0x5cf83c, 9, log)
    block = heap.string(data, 512)
    assert fw.fn('ringnav_cue_text', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p)(block, 0x11, 0x22) == 9
    assert list(log) == [block, 0x11, 0x22]


# SQL quoting.
def scan_quote(fw, heap, value, cap, n=0, prefix=b''):
    sql, at = heap.string(prefix, cap), C.c_uint(n)
    ok = fw.fn('tags__scan_quote', C.c_int, C.c_void_p, C.c_uint, C.POINTER(C.c_uint), C.c_char_p)(sql, cap, C.byref(at), value)
    return ok, at.value, sql


@pytest.mark.parametrize('value, quoted', [(b'', b"''"), (None, b"''"), (b'abc', b"'abc'"), (b"it's", b"'it''s'"), (b"'", b"''''"), (b"''", b"''''''"),
                                           (b'\xe9', b"'\xe9'")])
def test_scan_quote(fw, heap, value, quoted):
    ok, n, sql = scan_quote(fw, heap, value, 32)
    assert ok == 1 and n == len(quoted) and C.string_at(sql) == quoted


def test_scan_quote_appends(fw, heap):
    ok, n, sql = scan_quote(fw, heap, b'x', 32, 3, b'a=b')
    assert ok == 1 and n == 6 and C.string_at(sql) == b"a=b'x'"


@pytest.mark.parametrize('value, cap, ok', [
    (b'', 3, 0), (b'', 4, 1), (b'abc', 5, 0), (b'abc', 7, 1), (b"'", 4, 0), (b"'", 5, 1), (b"ab'", 7, 1), (b"ab'", 6, 0),
])
def test_scan_quote_refuses_what_wont_fit(fw, heap, value, cap, ok):
    assert scan_quote(fw, heap, value, cap)[0] == ok


def test_scan_quote_any_text_in_any_room(fw, heap):
    rng = random.Random(0x5c1)
    for data in hostile(rng, [b"o'clock", b"''''", b'plain', "'é'".encode()], 2500):
        cap = rng.randrange(1, 48)
        n = rng.randrange(0, cap)
        ok, end, sql = scan_quote(fw, heap, data, cap, n, b'x' * n)
        quoted = b"'" + data.replace(b"'", b"''") + b"'"
        if ok:
            assert C.string_at(sql)[n:] == quoted and end == n + len(quoted) and end < cap
        else:
            assert n + len(quoted) + 1 >= cap - 1  # refused only near the end, and never by much
        heap.free(sql)


def test_scrobble_tag_any_tag(fw, heap):
    tag = fw.fn('scrobble_tag', C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p)
    rng = random.Random(0x7a9)
    for data in hostile(rng, [b'a\tb', b'plain', b'\n\r\t'], 1500):
        room = rng.randrange(2, 48)
        line, value = heap.string(b'', room), heap.string(data)
        end = tag(line, line + room, value)
        kept = min(len(data), room - 1)
        assert end == line + kept + 1
        assert C.string_at(line, kept + 1) == data[:kept].translate(bytes.maketrans(b'\t\n\r', b'   ')) + b'\t'
        heap.free(line), heap.free(value)


# Paths.
@pytest.mark.parametrize('name, keep', [(b'Music', True), (b'.deezer', False), (b'..', False), (b'.', False), (b'a.b', True)])
def test_the_scan_skips_hidden_folders(fw, name, keep):
    assert bool(fw.fn('ringnav_scan_dir', C.c_int, C.c_char_p, C.c_char_p)(name, b'.')) == keep


def test_the_scan_any_folder_name(fw, heap):
    check = fw.fn('ringnav_scan_dir', C.c_int, C.c_void_p, C.c_char_p)
    for data in hostile(random.Random(0xd1), [b'Music', b'.hidden', b'..', b'a.b'], 1500):
        name = heap.string(data)
        assert bool(check(name, b'.')) == (data[:1] != b'.')
        heap.free(name)


@pytest.mark.parametrize('path, cue, m4b', [
    (None, 0, 0), (b'', 0, 0), (b'/a/b.m4b', 0, 1), (b'/a/b.M4B', 0, 1), (b'/a/b.m4a', 0, 0), (b'/a/m4b', 0, 0), (b'/a.m4b/b', 0, 0),
    (b'x.mp3.m4b', 0, 1), (b'x.m4b.mp3', 0, 0), (b'.m4b', 0, 1), (b'/a/b.m4b', 5, 1),
    (b'/mnt/mmc/Audiobooks/b.mp3', 0, 1), (b'/mnt/mmc/podcasts/a/b.ogg', 0, 1), (b'/mnt/mmc/Audiobooks/b.flac', 5, 0),
    (b'/mnt/mmc/Audiobooksx/b.mp3', 0, 0), (b'/mnt/mmc/Music/b.mp3', 0, 0), (b'/mnt/mmc/Books/b.mp3', 0, 0),
])
def test_chapter_file(fw, path, cue, m4b):
    """An M4B, or a spoken file that is not a CUE track (its sheet lists its chapters)."""
    strings(fw)
    assert fw.fn('navigation__chapter_file', C.c_int, C.c_void_p)(record([], path, cue=cue)) == m4b


def test_chapter_file_any_path(fw, heap):
    strings(fw)
    check = fw.fn('navigation__chapter_file', C.c_int, C.c_void_p)
    for data in hostile(random.Random(0xc4), [b'/a/b.m4b', b'/a/b.M4B', b'x.m4b.mp3', b'.m4b'], 1500):
        path = heap.string(data)
        r = C.create_string_buffer(0x60)
        struct.pack_into('<Q', r, REC_PATH, path)
        assert check(r) == (b'.' in data and data.rsplit(b'.', 1)[1].lower() == b'm4b')
        heap.free(path)


@pytest.mark.parametrize('text_, query, found', [
    (None, b'a', 0), (b'Song', b'on', 1), (b'SONG', b'on', 1), (b'song', b'ONG', 1), (b'song', b'x', 0), (b'song', b'', 1), (b'', b'', 1), (b'', b'a', 0),
])
def test_ple_has(fw, text_, query, found):
    assert fw.fn('playlists__ple_has', C.c_int, C.c_char_p, C.c_char_p)(text_, query) == found


def test_ple_has_any_text(fw, heap):
    has = fw.fn('playlists__ple_has', C.c_int, C.c_void_p, C.c_void_p)
    rng = random.Random(0x91e)
    seeds = [b'Song', b'SONG', b'ong', b'\xc3\xa9', b'']
    for data in hostile(rng, seeds + [b'x'], 1500, 24):
        query = rng.choice([data[:3], data[1:2], b'on', b'ONG', b'']) if data else b'a'
        a, b = heap.string(data), heap.string(query)
        assert has(a, b) == (query.translate(LOWER) in data.translate(LOWER))
        heap.free(a), heap.free(b)


@pytest.mark.parametrize('name, tidal, label', [
    (b'123-LOSSLESS Some Song.flac', 1, b'Some Song'),
    (b'123-LOSSLESS Mr. Big.flac', 1, b'Mr. Big'),
    (b'nospace.flac', 1, b'nospace'),
    (b'01 - Title [987].flac', 0, b'01 - Title'),
    (b'Title [not an id.flac', 0, b'Title [not an id'),
    (b'Title[987].flac', 0, b'Title[987]'),
    (b'[987].flac', 0, b'[987]'),
    (b' [987].flac', 0, b' [987]'),
    (b'a [987].flac', 0, b'a'),
    (b'a [1] [2].mp3', 0, b'a [1]'),
    (b'.hidden', 0, b'.hidden'),
    (b'noext', 0, b'noext'),
    (b'', 0, b''),
    (None, 0, b''),
    (None, 1, b''),
])
def test_song_label(fw, heap, name, tidal, label):
    out = heap.string(b'', 64)
    fw.fn('library_lists__song_label', None, C.c_char_p, C.c_int, C.c_void_p, C.c_uint)(name, tidal, out, 64)
    assert C.string_at(out) == label


def test_song_label_any_name_in_any_room(fw, heap):
    label = fw.fn('library_lists__song_label', None, C.c_void_p, C.c_int, C.c_void_p, C.c_uint)
    rng = random.Random(0x50)
    for data in hostile(rng, [b'123-Q Song.flac', b'01 - Title [987].flac', b'[', b' [', b'.', b' ]'], 2500):
        size = rng.randrange(1, 40)
        name, out = heap.string(data), heap.string(b'', size)
        label(name, rng.randrange(2), out, size)
        assert len(C.string_at(out)) < size
        heap.free(name), heap.free(out)


def test_scrobble_tag_keeps_a_line_one_line(fw):
    line = C.create_string_buffer(32)
    tag = fw.fn('scrobble_tag', C.c_void_p, C.c_void_p, C.c_void_p, C.c_char_p)
    end = tag(line, C.addressof(line) + 32, b'a\tb\nc\rd')
    assert line.raw[:8] == b'a b c d\t' and end == C.addressof(line) + 8


def test_scrobble_tag_stops_at_end(fw, heap):
    tag = fw.fn('scrobble_tag', C.c_void_p, C.c_void_p, C.c_void_p, C.c_char_p)
    line = heap.string(b'', 9)
    assert tag(line, line + 8, b'x' * 40) == line + 8
    assert C.string_at(line, 8) == b'x' * 7 + b'\t'


def test_scrobble_tag_of_nothing_is_an_empty_field(fw):
    line = C.create_string_buffer(8)
    tag = fw.fn('scrobble_tag', C.c_void_p, C.c_void_p, C.c_void_p, C.c_char_p)
    assert tag(line, C.addressof(line) + 8, None) == C.addressof(line) + 1 and line.raw[0:1] == b'\t'


# FLAC's album artist.
def block(kind, body, last=False):
    return bytes([kind | 0x80 * last]) + struct.pack('>I', len(body))[1:] + body


def comments(*items, vendor=b'libFLAC'):
    return (struct.pack('<I', len(vendor)) + vendor + struct.pack('<I', len(items)) +
            b''.join(struct.pack('<I', len(c)) + c for c in items))


def flac(*blocks):
    return b'fLaC' + b''.join(blocks)


GOOD = flac(block(0, b'\0' * 34), block(4, comments(b'TITLE=x', b'ALBUM ARTIST=The Band', b'ARTIST=y'), True))


def album_artist(fw, heap, data, name=b'/a/b.flac'):
    files = Files(fw, {name.decode(): data})
    out = heap.string(b'', 0x100)
    fw.fn('flac_album_artist', None, C.c_char_p, C.c_void_p)(name, out)
    return C.string_at(out), files


def test_flac_album_artist(fw, heap):
    assert album_artist(fw, heap, GOOD)[0] == b'The Band'


@pytest.mark.parametrize('key', [b'ALBUM ARTIST=', b'album artist=', b'Album Artist='])
def test_flac_album_artist_key_case_aside(fw, heap, key):
    assert album_artist(fw, heap, flac(block(4, comments(key + b'x'), True)))[0] == b'x'


@pytest.mark.parametrize('comment', [b'ALBUMARTIST=x', b'ALBUM ARTIST', b'ALBUM ARTIST=', b'ALBUM ARTISTS=x', b'A=b', b''])
def test_flac_other_comments_arent_it(fw, heap, comment):
    assert album_artist(fw, heap, flac(block(4, comments(comment, b'TITLE=z'), True)))[0] == b''


def test_flac_comment_after_short_ones(fw, heap):
    data = flac(block(4, comments(b'', b'A=b', b'ALBUM ARTIST', b'ALBUM ARTIST=first', b'ALBUM ARTIST=second'), True))
    assert album_artist(fw, heap, data)[0] == b'first'


def test_flac_comments_after_other_blocks(fw, heap):
    data = flac(block(0, b'\0' * 34), block(3, b'\0' * 18), block(6, b'\xff' * 400), block(4, comments(b'ALBUM ARTIST=x')))
    assert album_artist(fw, heap, data)[0] == b'x'


def test_flac_stops_at_the_last_block(fw, heap):
    data = flac(block(0, b'\0' * 34, True), block(4, comments(b'ALBUM ARTIST=x')))
    assert album_artist(fw, heap, data)[0] == b''


@pytest.mark.parametrize('size, kept', [(1, 1), (254, 254), (255, 255), (256, 255), (4000, 255)])
def test_flac_value_fits_its_0x100_bytes(fw, heap, size, kept):
    assert album_artist(fw, heap, flac(block(4, comments(b'ALBUM ARTIST=' + b'v' * size), True)))[0] == b'v' * kept


def test_flac_no_marker_no_tag(fw, heap):
    assert album_artist(fw, heap, b'ID3\x03' + GOOD[4:])[0] == b''


@pytest.mark.parametrize('name', [b'/a/b.mp3', b'/a/b', b'/a.flac/b'])
def test_flac_only_by_extension(fw, heap, name):
    assert album_artist(fw, heap, GOOD, name)[0] == b'' and not fw.calls('fw_fopen')


def test_flac_extension_case_aside(fw, heap):
    assert album_artist(fw, heap, GOOD, b'/a/b.FLAC')[0] == b'The Band'


def test_flac_missing_file(fw, heap):
    Files(fw)
    out = heap.string(b'', 0x100)
    fw.fn('flac_album_artist', None, C.c_char_p, C.c_void_p)(b'/a/b.flac', out)
    assert C.string_at(out) == b''


def test_flac_a_cut_value_is_no_value(fw, heap):
    assert album_artist(fw, heap, GOOD[:-20])[0] == b''


@pytest.mark.parametrize('vendor', [0xffffffff, 0x7fffffff, 1000])
def test_flac_a_vendor_length_past_the_block_stops(fw, heap, vendor):
    body = struct.pack('<I', vendor) + b'v' * 8 + struct.pack('<I', 1) + struct.pack('<I', 15) + b'ALBUM ARTIST=xy'
    assert album_artist(fw, heap, flac(block(4, body, True)))[0] == b''


def test_flac_a_comment_length_past_the_block_stops(fw, heap):
    body = struct.pack('<I', 0) + struct.pack('<I', 2) + struct.pack('<I', 0x7fffffff) + b'ALBUM ARTIST=xy'
    assert album_artist(fw, heap, flac(block(4, body, True)))[0] == b''


def test_flac_a_huge_comment_count_stops_with_the_block(fw, heap):
    body = struct.pack('<I', 0) + struct.pack('<I', 0xffffffff) + struct.pack('<I', 1) + b'x'
    assert album_artist(fw, heap, flac(block(4, body, True)) + b'\xff' * 64)[0] == b''


def flac_mutations(rng, base, count):
    for _ in range(count):
        data = bytearray(base)
        kind = rng.randrange(3)
        if kind == 0:
            del data[rng.randrange(len(data)):]
        for _ in range(rng.randrange(1, 6)):
            if data:
                data[rng.randrange(len(data))] = rng.choice((0, 0xff, 0x7f, 0x80, rng.randrange(256)))
        if kind == 2 and len(data) > 16:
            at = rng.randrange(len(data) - 4)
            data[at:at + 4] = struct.pack('<I', rng.choice((0, 1, 8, 13, 14, 0xff, 0x100, 0x7fffffff, 0xffffffff)))
        yield bytes(data)


def test_flac_any_file(fw, heap):
    scan = fw.fn('flac_album_artist', None, C.c_char_p, C.c_void_p)
    rng = random.Random(0xf1ac)
    files = Files(fw)
    long_value = flac(block(0, b'\0' * 34), block(4, comments(b'A=b', b'ALBUM ARTIST=' + b'v' * 300), True))
    cases = list(flac_mutations(rng, GOOD, 1500)) + list(flac_mutations(rng, long_value, 500))
    cases += [rng.randbytes(rng.randrange(64)) for _ in range(300)] + [b'fLaC' + rng.randbytes(rng.randrange(64)) for _ in range(500)]
    for data in cases:
        files.files['/a/b.flac'] = data
        out = heap.string(b'', 0x100)
        scan(b'/a/b.flac', out)
        assert len(C.string_at(out, 0x100).split(b'\0')[0]) < 0x100
        heap.free(out)
    assert not files.open  # every file closed


def opus(tags):
    """An Ogg Opus file: OpusHead, then tags as packet 1 on pages of its own (no CRC: xiph.c reads none)."""
    pages, seq = [], 0
    for packet in (b'OpusHead' + bytes([1, 2]) + struct.pack('<HIhB', 312, 48000, 0, 0), tags):
        lacing = [255] * (len(packet) // 255) + [len(packet) % 255]
        for at in range(0, len(lacing), 255):
            part = lacing[at:at + 255]
            pages.append(b'OggS\0' + bytes([at > 0]) + bytes(8) + struct.pack('<III', 1, seq, 0) + bytes([len(part)]) +
                         bytes(part) + packet[at * 255:at * 255 + sum(part)])
            seq += 1
    return b''.join(pages)


@pytest.mark.parametrize('size, big', [(1000, 0), ((4 << 20) + 1, 1)])
def test_ogg_tags_only_over_the_cap(fw, size, big):
    """xiph.c as the Q2 builds it, through the card's file calls: only a header over OGG_TAGS_MAX is
    its own (test/unit/test_xiph.py checks the fields it reads)."""
    files = Files(fw, {'/a/b.opus': opus(b'OpusTags' + comments(b'TITLE=x', b'P=' + b'v' * size))})
    assert fw.fn('ogg_tags', C.c_int, C.c_char_p, C.c_void_p)(b'/a/b.opus', None) == big
    assert not files.open


def test_music_info_fills_only_an_empty_album_artist(fw, heap):
    Files(fw, {'/a/b.flac': GOOD})
    info = heap.string(b'', 0x698)
    fw.fn('ringnav_music_info', C.c_int, C.c_int, C.c_void_p, C.c_char_p)(0, info, b'/a/b.flac')
    assert C.string_at(info + O['MUSIC_INFO_ALBUM_ARTIST']) == b'The Band'
    C.memmove(info + O['MUSIC_INFO_ALBUM_ARTIST'], b'Tagged\0', 7)
    fw.fn('ringnav_music_info', C.c_int, C.c_int, C.c_void_p, C.c_char_p)(0, info, b'/a/b.flac')
    assert C.string_at(info + O['MUSIC_INFO_ALBUM_ARTIST']) == b'Tagged'


def test_music_info_of_no_path(fw, heap):
    fw.mock('stock_music_info_trampoline', lambda *_: 3)
    assert fw.fn('ringnav_music_info', C.c_int, C.c_int, C.c_void_p, C.c_char_p)(0, heap.string(b'', 0x698), None) == 3
    assert not fw.calls('fw_fopen')


def id3_path(fw, heap, tags, cue, path=b'/a/b.flac'):
    id3 = heap.string(b'', 0xa84)
    for offset, value in tags.items():
        C.memmove(id3 + offset, value + b'\0', len(value) + 1)
    def info(_, out, *rest):
        for offset, value in ((0, b'file album'), (0x100, b'file artist'), (O['MUSIC_INFO_ALBUM_ARTIST'], b'file band')):
            C.memmove(out + offset, value + b'\0', len(value) + 1)
        return 1
    fw.mock('toolsGetMusicInfo', info)
    fw.mock('mclGetCueFlag', lambda *_: cue)
    return id3, fw.fn('ringnav_id3_path', C.c_void_p, C.c_void_p, C.c_char_p)(id3, path)


ALBUM, ARTIST, BAND = O['ID3_ALBUM'], O['ID3_ARTIST'], O['ID3_ALBUM'] + O['MUSIC_INFO_ALBUM_ARTIST']


def test_a_cue_track_takes_the_files_tags_for_what_its_sheet_lacks(fw, heap, mcl):
    id3, back = id3_path(fw, heap, {ALBUM: b'sheet album'}, 1)
    assert back == id3 and C.string_at(id3) == b'/a/b.flac'
    assert [C.string_at(id3 + f) for f in (ALBUM, ARTIST, BAND)] == [b'sheet album', b'file artist', b'file band']


def test_a_full_sheet_reads_no_file(fw, heap, mcl):
    id3_path(fw, heap, {ALBUM: b'a', ARTIST: b'b', BAND: b'c'}, 1)
    assert not fw.calls('toolsGetMusicInfo')


def test_no_cue_leaves_the_tags(fw, heap, mcl):
    id3, _ = id3_path(fw, heap, {}, 0)
    assert C.string_at(id3 + ALBUM) == b'' and C.string_at(id3) == b'/a/b.flac'


# Rows and records.
def record(keep, path=None, name=None, cue=0, end=0, kind=0, id_=0):
    r = C.create_string_buffer(0x60)
    struct.pack_into('<i', r, O['REC_TYPE'], kind)
    struct.pack_into('<i', r, O['REC_ID'], id_)
    for offset, value in ((REC_PATH, path), (REC_NAME, name)):
        if value is not None:
            chars = C.create_string_buffer(value)
            keep.append(chars)
            struct.pack_into('<Q', r, offset, C.addressof(chars))
    struct.pack_into('<ii', r, REC_CUE_START, cue, end)
    keep.append(r)
    return C.addressof(r)


def test_rec_hash_of_a_name_and_its_cue_range(fw):
    keep = []
    hash_to = fw.fn('navigation__rec_hash_to', C.c_uint, C.c_void_p, C.c_int)
    r = record(keep, name=b'song', cue=20, end=40)
    named = fnv1a(b'song') * FNV_PRIME & 0xffffffff
    assert hash_to(r, REC_NAME) == fnv1a(struct.pack('<ii', 20, 40), named)
    assert hash_to(record(keep, name=b'song', cue=21, end=40), REC_NAME) != hash_to(r, REC_NAME)
    assert hash_to(record(keep, name=b'son', cue=20, end=40), REC_NAME) != hash_to(r, REC_NAME)


def test_rec_hash_of_an_empty_record(fw):
    empty = C.create_string_buffer(0x60)
    h = fnv1a(b'')
    for _ in range(5):
        h = h * FNV_PRIME & 0xffffffff
    assert fw.fn('navigation__rec_hash', C.c_uint, C.c_void_p)(empty) == fnv1a(b'\0' * 8, h)


def test_rec_index_finds_the_first_of_a_path_and_cue_start(fw, heap):
    keep = []
    deques = Deques(fw)
    a, b, again, late = (record(keep, b'/a'), record(keep, b'/b'), record(keep, b'/a'), record(keep, b'/a', cue=40))
    none = record(keep)
    songs = deques.new([a, none, b, again, late])
    mask = C.c_uint()
    slot = fw.fn('rec_index', C.c_void_p, C.c_void_p, C.POINTER(C.c_uint))(songs, C.byref(mask))
    find = fw.fn('rec_find', C.c_void_p, C.c_void_p, C.c_void_p, C.c_uint, C.c_void_p)
    assert mask.value == 15  # at most half full
    assert find(songs, slot, 15, record(keep, b'/a')) == a and find(songs, slot, 15, record(keep, b'/b')) == b
    assert find(songs, slot, 15, record(keep, b'/a', cue=40)) == late
    assert find(songs, slot, 15, record(keep, b'/a', cue=41)) is None
    assert find(songs, slot, 15, record(keep, b'/c')) is None and find(songs, slot, 15, record(keep)) is None
    heap.free(slot)


def test_rec_index_of_nothing(fw, heap):
    deques = Deques(fw)
    mask = C.c_uint()
    slot = fw.fn('rec_index', C.c_void_p, C.c_void_p, C.POINTER(C.c_uint))(deques.new(), C.byref(mask))
    assert mask.value == 1 and C.string_at(slot, 8) == b'\0' * 8
    heap.free(slot)


class TdRow(C.Structure):
    _fields_ = [('mtime', C.c_long), ('r', C.c_void_p)]


def test_td_newer_sorts_newest_first(fw):
    newer = fw.fn('library_lists__td_newer', C.c_int, C.POINTER(TdRow), C.POINTER(TdRow))
    a, b = TdRow(5, None), TdRow(9, None)
    assert newer(a, b) == 1 and newer(b, a) == -1 and newer(a, a) == 0


def test_td_named_sorts_by_name_and_no_name_first(fw):
    keep = []
    named = fw.fn('library_lists__td_named', C.c_int, C.POINTER(TdRow), C.POINTER(TdRow))
    a, b, none = TdRow(0, record(keep, name=b'01')), TdRow(0, record(keep, name=b'02')), TdRow(0, record(keep))
    assert named(a, b) < 0 and named(b, a) > 0 and named(a, a) == 0 and named(none, a) < 0


def test_card_songs_newest_first(fw):
    keep = []
    deques = Deques(fw)
    rows = [record(keep, b'/m/%d' % i, kind=8 if i < 2 else 4) for i in range(3)]
    fw.mock('toolsLoadDirectory', lambda *_: deques[directory(fw)].extend(rows) and 0)
    def stat(version, path, info, *_):
        C.c_uint.from_address(info + 64).value = {b'/m/0': 5, b'/m/1': 9}[C.string_at(path)]
        return 0
    fw.mock('fw___xstat', stat)
    out = fw.fn('library_lists__card_songs', C.c_long, C.c_char_p, C.c_int)(b'/m', 0)
    assert deques[out] == [rows[1], rows[0]]  # a row that isn't a song is left out


def directory(fw):
    """The deque stock's queries fill: staged() puts its own there for each query (Deques masks the high half)."""
    return fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value


class Tree:
    """toolsLoadDirectory over a dict of folders, into staged's deque."""
    def __init__(self, fw, folders):
        self.deques, self.folders, self.keep = Deques(fw), folders, []
        fw.mock('toolsLoadDirectory', lambda path, *_: self.deques[directory(fw)].__setitem__(slice(None), self.rows(C.string_at(path))))

    def rows(self, path):
        return [record(self.keep, b'%s/%s' % (path, name), kind=8) if kind == 8 else record(self.keep, name=name, kind=kind)
                for name, kind in self.folders.get(path, ())]


def folder_tracks(fw, tree, path):
    add = tree.deques.new()
    fw.fn('navigation__folder_tracks', None, C.c_char_p, C.c_void_p, C.c_int)(path, add, 0)
    return [C.string_at(struct.unpack_from('<Q', C.string_at(r, 0x60), REC_PATH)[0]) for r in tree.deques[add]]


def test_a_folder_queues_its_songs_and_its_subfolders(fw):
    tree = Tree(fw, {b'/m': [(b'a.flac', 8), (b'sub', 4), (b'.tidal', 4), (b'clip.mp4', 8), (b'b.flac', 8)],
                     b'/m/sub': [(b'c.flac', 8)], b'/m/.tidal': [(b'no.flac', 8)]})
    assert folder_tracks(fw, tree, b'/m') == [b'/m/a.flac', b'/m/sub/c.flac', b'/m/b.flac']


def test_a_folder_walks_eight_levels_down(fw):
    folders, path = {}, b'/m'
    for depth in range(12):
        folders[path] = [(b'%d.flac' % depth, 8), (b'd', 4)]
        path += b'/d'
    assert len(folder_tracks(fw, Tree(fw, folders), b'/m')) == 9


def test_a_subfolder_past_1023_bytes_is_passed_over(fw):
    fits, over = b'd' * 1020, b'd' * 1021
    tree = Tree(fw, {b'/m': [(fits, 4), (over, 4)], b'/m/' + fits: [(b'a', 8)], b'/m/' + over: [(b'b', 8)]})
    assert folder_tracks(fw, tree, b'/m') == [b'/m/' + fits + b'/a']


def qm_folder_tracks(fw, st, tree, folder, name):
    """qm_tracks for a Folder row named name while g_folder_path is folder: the paths it adds."""
    st('qm_kind').value = 4  # QM_FOLDER
    C.memmove(C.addressof(fw.var('fw_ctx_g_folder_path', C.c_char)), folder + b'\0', len(folder) + 1)
    add = tree.deques.new()
    fw.fn('navigation__qm_tracks', None, C.c_void_p, C.c_void_p)(record(tree.keep, name=name, kind=4), add)
    return [C.string_at(struct.unpack_from('<Q', C.string_at(r, 0x60), REC_PATH)[0]) for r in tree.deques[add]]


def test_a_queued_folder_whose_path_is_cut_adds_nothing(fw, st):
    """qm_tracks joins g_folder_path and the row's name into 1024 bytes. A cut path is a prefix, here the
    folder beside it, whose songs were queued for the wrong row: so, as folder_tracks passes such a subfolder
    over, the row adds nothing."""
    folder = b'/m/' + b'f' * 1000
    fits, over = b'n' * 19, b'n' * 20  # 1003 + 1 + 19 bytes fit; one more is cut to the folder beside it
    tree = Tree(fw, {folder + b'/' + fits: [(b'a', 8)], folder + b'/' + over: [(b'b', 8)]})
    assert qm_folder_tracks(fw, st, tree, folder, fits) == [folder + b'/' + fits + b'/a']
    assert qm_folder_tracks(fw, st, tree, folder, over) == []


def song_folder(fw, heap, path, stat=0, mode=0o040755):
    keep = []
    def xstat(version, where, info, *_):
        C.c_uint.from_address(info + 20).value = mode
        return stat
    fw.mock('fw___xstat', xstat)
    out = heap.string(b'', 1024)
    got = fw.fn('navigation__song_folder', C.c_int, C.c_void_p, C.c_void_p)(record(keep, path), out)
    return got, C.string_at(out), out


@pytest.mark.parametrize('path, layer, folder', [
    (b'/mnt/mmc/a.flac', 2, b'/mnt/mmc'), (b'/mnt/mmc/A/b.flac', 3, b'/mnt/mmc/A'), (b'/mnt/mmc/A/B/c.flac', 4, b'/mnt/mmc/A/B'),
    (b'/mnt/mmc/..a/b.flac', 3, b'/mnt/mmc/..a'), (b'/mnt/mmc/.a/b.flac', 3, b'/mnt/mmc/.a'), (b'/mnt/mmc/a./b.flac', 3, b'/mnt/mmc/a.'),
    (b'/mnt/mmc/.../b.flac', 3, b'/mnt/mmc/...'),
])
def test_song_folder(fw, heap, path, layer, folder):
    got, value, _ = song_folder(fw, heap, path)
    assert got == layer and value == folder


@pytest.mark.parametrize('path', [
    b'/mnt/mmc/', b'/mnt/mmc', b'/mnt/usb/a.flac', b'mnt/mmc/a.flac', b'/mnt/mmc/./a.flac', b'/mnt/mmc/../a.flac', b'/mnt/mmc//a.flac',
    b'/mnt/mmc/a/../b.flac', b'/mnt/mmc/a//b.flac', b'/mnt/mmc/a/./b.flac',
])
def test_song_folder_refuses(fw, heap, path):
    assert song_folder(fw, heap, path)[0] == 0


def test_song_folder_of_a_record_with_no_path(fw, heap):
    assert song_folder(fw, heap, None)[0] == 0


@pytest.mark.parametrize('stat, mode', [(-1, 0o040755), (0, 0o100644)])
def test_song_folder_must_still_be_a_folder(fw, heap, stat, mode):
    assert song_folder(fw, heap, b'/mnt/mmc/a/b.flac', stat, mode)[0] == 0


@pytest.mark.parametrize('names, layer', [(253, 255), (254, 0)])
def test_song_folder_layer_is_a_byte(fw, heap, names, layer):
    assert song_folder(fw, heap, b'/mnt/mmc/' + b'a/' * names + b'f.flac')[0] == layer


@pytest.mark.parametrize('size, fits', [(1023, True), (1024, False)])
def test_song_folder_fits_g_folder_path(fw, heap, size, fits):
    folder = b'/mnt/mmc/' + b'a' * (size - 9)
    got, value, _ = song_folder(fw, heap, folder + b'/f.flac')
    assert bool(got) == fits and (not fits or value == folder)


def test_song_folder_any_path(fw, heap):
    rng = random.Random(0xf0d)
    for data in hostile(rng, [b'/mnt/mmc/a/b.flac', b'/mnt/mmc/./a', b'/mnt/mmc/../a', b'/mnt/mmc//a', b'/mnt/mmc/x', b'/mnt/mmc/'], 2000):
        got, _, out = song_folder(fw, heap, data)
        names = data[:data.rfind(b'/')][9:].split(b'/') if data.rfind(b'/') > 8 else []
        good = len(data) > 9 and data[:9] == b'/mnt/mmc/' and not {b'', b'.', b'..'} & set(names)
        assert got == (2 + len(names) if good else 0)
        heap.free(out)


def test_toast_cuts_a_long_text(fw, heap):
    seen = []
    def show(name, info, *_):
        seen.append((C.string_at(name), C.string_at(info, 0x408)))
    fw.mock('navigator_to_with_context', show)
    fw.fn('toast', None, C.c_void_p)(heap.string(b'x' * 2000))
    assert seen[0][0] == b'dialog/msginfo_dialog'
    assert struct.unpack_from('<2i', seen[0][1]) == (1, 2000) and seen[0][1][8:] == b'x' * 0x3ff + b'\0'


class Wifi:
    def __init__(self, fw, data, fd=4):
        self.data, self.now = data, 5000
        fw.mock('fw_open', lambda *_: fd)
        fw.mock('fw_read', self.read)
        fw.mock('time_now_ms', lambda *_: self.now)
        self.signal = fw.fn('ringnav_wifi_signal', C.c_int)

    def read(self, fd, out, size, *_):
        C.memmove(out, self.data[:size], min(size, len(self.data)))
        return min(size, len(self.data))


@pytest.mark.parametrize('data, dbm', [
    (b'signal_strength:75\n', -25), (b'x\nsignal_strength:0\n', -100), (b'signal_strength:100', 0), (b'signal_strength:', -100),
    (b'no such line', 0), (b'', 0), (b'x' * 600 + b'signal_strength:9', 0), (b'x' * 490 + b'signal_strength:9', -91),
    (b'x' * 494 + b'signal_strength:9', -91), (b'x' * 495 + b'signal_strength:9', -100),  # read cut after the colon
])
def test_wifi_signal(fw, data, dbm):
    assert Wifi(fw, data).signal() == dbm


def test_wifi_signal_with_no_file(fw):
    assert Wifi(fw, b'signal_strength:75', fd=-1).signal() == 0 and not fw.calls('fw_close')


def test_wifi_signal_is_read_once_a_second(fw):
    wifi = Wifi(fw, b'signal_strength:75')
    assert wifi.signal() == -25
    wifi.data, wifi.now = b'signal_strength:50', 5999
    assert wifi.signal() == -25
    wifi.now = 6000
    assert wifi.signal() == -50


def test_wifi_signal_at_a_zero_clock_still_counts_as_read(fw):
    wifi = Wifi(fw, b'signal_strength:75')
    wifi.now = 0
    assert wifi.signal() == -25
    wifi.data, wifi.now = b'signal_strength:50', 500
    assert wifi.signal() == -25


def test_wifi_signal_any_bytes(fw):
    wifi = Wifi(fw, b'')
    rng = random.Random(0x3d)
    for data in hostile(rng, [b'signal_strength:75\n', b'rssi:-40\nsignal_strength:100\n'], 1500, 600):
        wifi.data, wifi.now = data, wifi.now + 2000
        wifi.signal()


STALL, STALL_POLL_MS, STALL_MS, STALL_AGAIN_MS = b'/mnt/data/q2-stall', O['STALL_POLL_MS'], O['STALL_MS'], O['STALL_AGAIN_MS']
STALL_PC = 0x2ab4c5d0
STALL_MEMORY = b'MemAvailable:       3400 kB\npgmajfault 1234\nworkingset_refault_file 678\n'
# the stack's words that could be code (0x400000 up to 0x80000000, word-aligned), as q2remote --prof's line
# keeps them, after tid, state, pc and an unknown ra
STALL_STACK = b'812 D %x 0 4a1234 2ab00010\n' % STALL_PC


class Stall:
    """The stall thread's world (system.c stall_step): /proc's files as bytes, the UI thread's stack
    (a real block: the C reads it in place) with stat's startstack `top` bytes above its sp, the record's
    opens, syncs and bytes, and now_ms's clock. A UI loop pass is wdt_poll; the first arms the watchdog,
    which starts the thread (only recorded here), so its wakes are stall_step, run by hand."""
    def __init__(self, fw, power, syscall=None, top=4 * 160 + 4096):
        self.now, self.fd, self.handles, self.log = 100000, 10, {}, []
        self.record: bytes | None = None  # the record file's bytes; None, no file
        self.stack = (C.c_uint * 160)(0x4a1234, 0x10, 0x2ab00010, 0x80001000, 0x4a1235)
        self.sp = C.addressof(self.stack)
        # read(5, buf, 1024), then sp and pc
        self.syscall = b'4003 0x5 0x7fd2a0 0x400 0x0 0x0 0x0 %#x %#x' % (self.sp, STALL_PC) if syscall is None else syscall
        self.files = {b'/proc/self/stat': b'812 (demo) D 1 812 812 0 -1 4194560 9000 0 1200 0 300 40 0 0 20 0 12 0 900 '
                                          b'33554432 2100 4294967295 4194304 11534336 %d 0 0\n' % (self.sp + top),
                      b'/proc/self/wchan': b'wait_on_page_bit_common', b'/proc/self/syscall': self.syscall + b'\n',
                      b'/proc/meminfo': b'MemTotal:         118000 kB\nMemFree:            2100 kB\nMemAvailable:       3400 kB\n'
                                        b'Buffers:               0 kB\n',
                      b'/proc/vmstat': b'nr_free_pages 525\nworkingset_refault_anon 0\nworkingset_refault_file 678\n'
                                       b'pgfault 99000\npgmajfault_x 1\npgmajfault 1234\n'}
        fw.mock('fw_clock_gettime', self.clock)
        fw.mock('fw_open', self.open)
        fw.mock('fw_read', self.read)
        fw.mock('fw_write', self.write)
        fw.mock('fw_fsync', lambda fd, *_: self.log.append(('fsync', len(self.record or b''))))
        fw.mock('fw_close', lambda fd, *_: self.handles.pop(fd) and 0)
        fw.mock('fw_unlink', self.unlink)
        self.step = fw.fn('system__stall_step', None)
        self.pass_ = fw.fn('system__wdt_poll', None)
        self.pass_()
        thread = fw.calls('fw_pthread_create')
        assert power('wdt').value == 3 and len(thread) == 1 and thread[0][1][2] == C.cast(fw.lib.system__stall_watch, C.c_void_p).value

    def expect(self, age, toasts=0, stack=STALL_STACK):
        """What the UI thread was doing, as the files say."""
        return (b'age %d ms\ntoasts %d\nstate D\nwchan wait_on_page_bit_common\nsyscall %s\n' % (age, toasts, self.syscall)
                + STALL_MEMORY + stack)

    def clock(self, which, ts, *_):
        t = C.cast(ts, C.POINTER(C.c_long))  # now_ms's struct of two longs
        t[0], t[1] = self.now // 1000, self.now % 1000 * 1000000

    def open(self, path, flags, *_):
        path = C.string_at(path)
        if path == b'/dev/jz_watchdog':
            return 3
        if path == STALL:
            self.log.append(('open', flags))
            if flags & 0x200 or self.record is None:  # O_TRUNC, or O_CREAT's new file
                self.record = b''
        elif path not in self.files:
            return -1
        self.fd += 1
        self.handles[self.fd] = [path, 0]
        return self.fd

    def read(self, fd, out, size, *_):
        path, at = self.handles[fd]
        data = self.files[path][at:at + size]
        C.memmove(out, data, len(data))
        self.handles[fd][1] += len(data)
        return len(data)

    def write(self, fd, data, n, *_):
        assert self.handles[fd][0] == STALL and self.record is not None
        self.record += C.string_at(data, n)
        return n

    def unlink(self, path, *_):
        self.log.append(('unlink', C.string_at(path)))
        if C.string_at(path) == STALL:
            self.record = None
        return 0

    def wait(self, ms):
        """The thread's wakes over ms."""
        for _ in range(ms // STALL_POLL_MS):
            self.now += STALL_POLL_MS
            self.step()

    def opens(self):
        return [e[1] for e in self.log if e[0] == 'open']


def test_stall_record_at_20_and_45_s(fw, power):
    s = Stall(fw, power)
    s.step()  # the thread's first wake after the pass: the time counts from here
    s.wait(STALL_MS - STALL_POLL_MS)
    assert s.record is None
    s.wait(STALL_POLL_MS)
    first = s.expect(STALL_MS)
    assert s.record == first and s.log == [('open', 0x80301), ('fsync', len(first))]  # O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, synced
    s.wait(STALL_AGAIN_MS - STALL_MS - STALL_POLL_MS)
    assert s.record == first
    s.wait(STALL_POLL_MS)
    both = first + s.expect(STALL_AGAIN_MS)
    assert s.record == both and s.opens() == [0x80301, 0x80109] and s.log[-1] == ('fsync', len(both))  # appended: O_APPEND
    s.wait(3 * STALL_POLL_MS)
    assert s.record == both and len(s.opens()) == 2  # two at most: the watchdog resets at 60 s
    assert STALL_AGAIN_MS + STALL_POLL_MS < O['WDT_MS']


def test_stall_record_counts_toasts_open(fw, st, power):
    s = Stall(fw, power)
    toast = fw.fn('toast', None, C.c_char_p)
    depth = []
    def show(name, info, *_):
        depth.append(st('toasts').value)
        if len(depth) < 2:
            toast(b'again')  # a poll in the dialog's loop toasts again
        else:
            s.step()
            s.wait(STALL_MS)  # and that one's loop stalls
    fw.mock('navigator_to_with_context', show)
    toast(b'Could not save boot logo')
    assert depth == [1, 2] and st('toasts').value == 0 and s.record == s.expect(STALL_MS, toasts=2)


def test_stall_record_goes_when_the_ui_passes(fw, power):
    s = Stall(fw, power)
    s.step()
    s.wait(STALL_MS)
    assert s.record == s.expect(STALL_MS)
    s.pass_()
    s.step()
    assert s.record is None and s.log[-1] == ('unlink', STALL)  # a stall that recovered isn't what it's for
    s.wait(STALL_MS - STALL_POLL_MS)
    assert s.record is None
    s.wait(STALL_POLL_MS)
    assert s.record == s.expect(STALL_MS) and s.opens() == [0x80301, 0x80301]  # the next stall's, afresh
    s.pass_()
    s.step()
    s.log.clear()
    for _ in range(3):
        s.pass_()
        s.step()
        s.wait(STALL_POLL_MS)
    assert s.log == []  # no record: nothing to remove


def test_a_clean_end_removes_a_stall_record_the_thread_hadn_t_yet(fw, power):
    """The UI recovers and Rockbox opens before the thread's next wake: S90play's loop starts demo again with no boot
    between, so nothing else would remove it, and a later forced restart would report this stall."""
    s = Stall(fw, power)
    s.step()
    s.wait(STALL_MS)
    assert s.record == s.expect(STALL_MS)
    s.pass_()
    fw.fn('ringnav_clean_end', None)()
    assert s.record is None and ('unlink', STALL) in s.log


def test_a_standby_wake_drops_the_card_toasts(fw, st, power):
    now, toast = [5000], C.c_byte.from_address(O['CARD_TOAST'])
    fw.mock('time_now_ms', lambda *_: now[0])
    def card(*_):
        toast.value = 1
        return 0
    fw.mock('stock_cardmount_trampoline', card)
    check = fw.fn('ringnav_cardmount', C.c_int)
    toast.value = 0
    check()
    assert toast.value == 1  # no Standby yet: a real swap
    st('pod_read').value = 1  # no config.ini to read
    fw.mock('stock_standby_trampoline', lambda *_: 0)
    fw.fn('ringnav_standby', C.c_int)()
    now[0] += O['CARD_QUIET_MS'] - 1
    check()
    assert toast.value == 0
    now[0] += 1
    check()
    assert toast.value == 1  # the window is over


def test_a_modal_dialogs_close_skips_a_dialog_already_freed(fw):
    """dialog_modal's close (DIALOG_CLOSE) closes its dialog while the window manager still holds it,
    and touches nothing once it is gone: stock's close read a freed dialog's parent (q2-crash.log)."""
    wm, dialog, page = 0x1000, 0x2000, 0x3000
    windows, queued = [page, dialog], []
    fw.mock('window_manager', lambda *_: wm)
    fw.mock('widget_count_children', lambda w, *_: len(windows) if w == wm else 0)
    fw.mock('widget_get_child', lambda w, i, *_: windows[i])
    fw.mock('idle_add', lambda f, ctx, *_: queued.append((f, ctx)) or 1)
    close = fw.fn('system__dialog_close', C.c_int, C.c_void_p)
    fw.fn('ringnav_dialog_close', C.c_int, C.c_void_p, C.c_void_p)(0x6521dc, dialog)
    assert queued == [(C.cast(close, C.c_void_p).value, dialog)]
    idle = (C.c_char * 0x28)()
    C.c_void_p.from_buffer(idle, O['IDLE_CTX']).value = dialog
    close(C.addressof(idle))
    assert [args[0] for _, args in fw.calls('window_close')] == [dialog]
    windows.remove(dialog)  # widget_destroy takes it out of the window manager before freeing it
    close(C.addressof(idle))
    assert len(fw.calls('window_close')) == 1


@pytest.mark.parametrize('stop', ['standby', 'long pass', 'clean end'])
def test_no_stall_record_while_the_watchdog_is_stopped_or_stretched(fw, st, stop, power):
    s = Stall(fw, power)
    s.step()
    if stop == 'standby':
        st('pod_read').value = 1  # no config.ini to read
        fw.mock('stock_standby_trampoline', lambda *_: s.wait(2 * O['WDT_MS']))  # the thread wakes on, around the suspend
        fw.fn('ringnav_standby', C.c_int)()
        s.step()  # the wake just after: the time counts from the resume
    else:
        fw.fn('ringnav_wdt_long' if stop == 'long pass' else 'ringnav_clean_end', None)()
        s.wait(2 * O['WDT_MS'])
    assert s.record is None and s.opens() == []
    if stop == 'long pass':
        s.pass_()  # after it, WDT_MS again
        s.step()
    if stop != 'clean end':
        s.wait(STALL_MS)  # a stall from there on is caught
        assert s.record == s.expect(STALL_MS)
    else:
        s.wait(2 * O['WDT_MS'])
        assert s.record is None


@pytest.mark.parametrize('top, stack', [
    (12, STALL_STACK),                         # the words below the main stack's top only: 3 of them
    (8, b'812 D %x 0 4a1234\n' % STALL_PC),
    (0, b''), (-4, b''),                       # an sp at or past the top
    (0x800000, b''),                           # one 8 MB below it: another thread's stack, not read
])
def test_stall_record_reads_only_the_main_stack(fw, top, stack, power):
    s = Stall(fw, power, top=top)
    s.step()
    s.wait(STALL_MS)
    assert s.record == s.expect(STALL_MS, stack=stack)


@pytest.mark.parametrize('case', ['on the cpu', 'no startstack', 'unaligned sp'])
def test_stall_record_with_no_stack_to_read(fw, case, power):
    s = Stall(fw, power, syscall=b'running' if case == 'on the cpu' else None)  # registers only for a blocked thread
    want = s.expect(STALL_MS, stack=b'')
    if case == 'on the cpu':
        del s.files[b'/proc/self/wchan']  # a kernel without CONFIG_KALLSYMS
        want = want.replace(b'wchan wait_on_page_bit_common\n', b'wchan \n')
    if case == 'no startstack':
        s.files[b'/proc/self/stat'] = b'812 (a) b) D 1 812\n'  # the state after the last ')'
    if case == 'unaligned sp':
        s.syscall = s.syscall.replace(b'%#x' % s.sp, b'%#x' % (s.sp + 2))
        s.files[b'/proc/self/syscall'] = s.syscall + b'\n'
        want = s.expect(STALL_MS, stack=b'')
    s.step()
    s.wait(STALL_MS)
    assert s.record == want


class Clock:
    def __init__(self, fw, clock):
        self.labels, self.timers, self.time = [], [], (0, 0, 12)
        Config(fw)
        def local(when, out, *_):
            for i, v in enumerate(self.time):
                C.c_int.from_address(out + 4 * i).value = v
            return out
        fw.mock('fw_localtime_r', local)
        fw.mock('widget_lookup', lambda *_: 0x40)
        fw.mock('widget_set_text_utf8', lambda label, chars, *_: self.labels.append(C.string_at(chars)) and 0)
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append(ms) or 9)
        fw.var('fw_ctx_system_bar', C.c_uint).value = 0x30
        self.show = fw.fn('navigation__clock_show', None)


@pytest.mark.parametrize('hour, minute, twelve, full', [
    (18, 14, b'6:14 PM', b'18:14'), (6, 5, b'6:05 AM', b'06:05'), (0, 5, b'12:05 AM', b'00:05'), (12, 0, b'12:00 PM', b'12:00'),
    (11, 59, b'11:59 AM', b'11:59'), (23, 59, b'11:59 PM', b'23:59'),
])
def test_the_clock_label(fw, clock, hour, minute, twelve, full):
    label = Clock(fw, clock)
    label.time = (10, minute, hour)
    label.show()
    assert label.labels == [twelve]
    fw.var('g_time24h_flag', C.c_ubyte).value = 1
    label.show()
    assert label.labels == [twelve, full]


def test_the_clock_is_due_at_the_next_minute(fw, clock):
    label = Clock(fw, clock)
    label.time = (45, 1, 2)
    label.show()
    assert label.timers == [15000]


def test_the_clock_writes_only_a_new_minute(fw, clock):
    label = Clock(fw, clock)
    label.show(), label.show()
    assert len(label.labels) == 1
    label.time = (0, 1, 12)
    label.show()
    assert label.labels[-1] == b'12:01 PM' and len(label.labels) == 2


@pytest.mark.parametrize('time', [(0, 0, 24), (0, 60, 1), (61, 0, 1), (-1, 0, 1)])
def test_a_time_that_cant_be_shows_dashes(fw, clock, time):
    label = Clock(fw, clock)
    label.time = time
    label.show()
    assert label.labels == [b'--:--'] and label.timers == [60000]


def test_no_clock_shows_dashes(fw, clock):
    label = Clock(fw, clock)
    clock['hour'] = None
    label.show()
    assert label.labels == [b'--:--']


def test_auto_light_mode_turns_with_the_clock(fw, clock):
    label = Clock(fw, clock)
    Config(fw, LIGHT=b'2')
    assert fw.fn('ipod_light', C.c_int)() == 1
    label.time = (0, 0, 19)
    label.show()
    assert fw.fn('ipod_light', C.c_int)() == 0 and len(fw.calls('image_manager_unload_all')) == 1
    label.time = (0, 1, 19)
    label.show()
    assert len(fw.calls('image_manager_unload_all')) == 1


# Hidden services.
class Services:
    def __init__(self, fw, st, scratch, **config):
        self.keep, self.written = [C.create_string_buffer(0x60) for _ in range(2)], []
        struct.pack_into('<Q', self.keep[0], O['W_PARENT'], C.addressof(self.keep[1]))
        base = C.addressof(fw.var('st', C.c_char)) + scratch['sc_check']
        for i in range(13):
            C.c_void_p.from_address(base + 8 * i).value = C.addressof(self.keep[0])
        self.config = Config(fw, **config)
        fw.mock('toolsWriteConfig', lambda path, section, key, value, *_: self.written.append(C.string_at(value)) and 0)
        self.toggle = fw.fn('navigation__svc_toggle', C.c_int, C.c_int)


def test_a_service_hides_and_shows_again(fw, st, scratch):
    services = Services(fw, st, scratch, STREAMHIDE=b'TIDAL')
    services.toggle(3)
    assert services.written == [b'TIDAL,RADIO']
    services.config.values[b'STREAMHIDE'] = b'TIDAL,RADIO'
    services.toggle(0)
    assert services.written[-1] == b'RADIO'


def test_a_full_list_refuses_with_a_toast(fw, st, scratch):
    services = Services(fw, st, scratch, STREAMHIDE=b'x' * 194)
    seen = []
    fw.mock('navigator_to_with_context', lambda name, *_: seen.append(C.string_at(name)) and 0)
    services.toggle(3)  # 194 + , + RADIO is ROOT_MAX: stock's config line can't keep more
    assert not services.written and seen == [b'dialog/msginfo_dialog']


def test_a_gone_app_has_nothing_to_toggle(fw, st, scratch):
    services = Services(fw, st, scratch, STREAMHIDE=b'x')
    st('svc_n').value = 1
    services.toggle(2)
    assert not services.written


def test_no_streamhide_key_hides_nothing(fw):
    """Stock leaves out alone for a missing key with an empty default (root_read's note): svc_hidden
    empties it first, whatever the stack held."""
    seen = []
    fw.mock('toolsReadConfig', lambda path, section, key, out, *_: seen.append(C.string_at(out)))  # no key: out kept
    hide = text(b'stale')
    fw.fn('svc_hidden', None, C.c_char_p)(hide)
    assert seen == [b''] and hide.value == b''


def test_root_click_saves_the_next_folder(fw):
    Card(fw, FOLDERS)
    Config(fw, FOLDERROOT=b'art')
    seen = []
    fw.mock('toolsWriteConfig', lambda path, section, key, value, *_: seen.append((C.string_at(key), C.string_at(value))) and 0)
    fw.fn('navigation__root_click', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert seen == [(b'FOLDERROOT', b'Music')]


# The queue menu's last step.
QM_SONG, QM_FOLDER, QM_COVERFLOW = 1, 4, 5


def qm_apply(fw, st, deques, add, next_, kind, cls=0xf00a):
    st('qm_kind').value, st('qm_cls', C.c_uint).value = kind, cls
    return fw.fn('navigation__qm_apply', C.c_int, C.c_void_p, C.c_int)(deques.new(add), next_)


@pytest.fixture
def queue(fw, mcl):
    deques = Deques(fw)
    handle = deques.new()
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = handle
    C.c_uint64.from_address(O['MCL_POOL']).value = deques.new()
    mcl.type = 1
    return deques, handle


@pytest.mark.parametrize('kind, cls', [(QM_SONG, 0xf00a), (QM_FOLDER, 1)])
def test_an_empty_queue_loads_without_playing(fw, st, queue, kind, cls):
    deques, _ = queue
    assert qm_apply(fw, st, deques, [0x10], 1, kind) == 1
    load = fw.calls('mclLoadPlayList')
    assert len(load) == 1 and load[0][1][1:3] == (0, cls) and not fw.calls('mclStartPlayer')


def test_add_grows_a_local_queue(fw, st, queue):
    deques, handle = queue
    deques[handle].extend([1, 2])
    assert qm_apply(fw, st, deques, [3], 0, QM_SONG) == 1 and deques[handle] == [1, 2, 3]


@pytest.mark.parametrize('kind', [0, 0x2000, 0x3001])
def test_a_stream_keeps_its_queue(fw, st, queue, mcl, kind):
    deques, handle = queue
    deques[handle].extend([1, 2])
    mcl.type = kind
    assert qm_apply(fw, st, deques, [3], 0, QM_SONG) == 0 and deques[handle] == [1, 2]


def test_airplay_keeps_its_queue(fw, st, queue):
    deques, handle = queue
    deques[handle].append(1)
    fw.mock('airplayGetFlag', lambda *_: 2)
    assert qm_apply(fw, st, deques, [3], 0, QM_SONG) == 0


def test_nothing_to_add_is_no_action(fw, st, queue):
    deques, handle = queue
    assert qm_apply(fw, st, deques, [], 0, QM_SONG) == 0 and not fw.calls('mclLoadPlayList')


def test_no_queue_is_no_action(fw, st, mcl):
    deques = Deques(fw)
    assert qm_apply(fw, st, deques, [1], 0, QM_SONG) == 0


def test_shuffle_play_takes_a_random_start_and_the_saved_mode(fw, queue, mcl):
    deques, _ = queue
    mcl.mode = 3
    seen = []
    fw.mock('navigator_to_with_context', lambda name, context, *_: seen.append(C.string_at(context + 8, 8)) and 0)
    play = fw.fn('shuffle_play', C.c_int, C.c_void_p)
    assert play(deques.new([1, 2, 3])) == 3
    index, cls = struct.unpack('<2i', seen[0])
    assert 0 <= index < 3 and cls == 0xf001 and [c[1][:2] for c in fw.calls('config_playmode')] == [(2, 1)]


def test_shuffle_play_of_nothing_does_nothing(fw, queue, mcl):
    deques, _ = queue
    assert fw.fn('shuffle_play', C.c_int, C.c_void_p)(deques.new()) == 0 and not fw.calls('config_playmode')


# Most Played.
def listen_key(path, cue=0):
    key = fnv1a(path) * FNV_PRIME & 0xffffffff
    if cue:
        key = fnv1a(struct.pack('<i', cue), key)
    return key or 1


def test_most_played_ranks_by_count_then_recency(fw, st, scratch):
    keep, deques = [], Deques(fw)
    songs = [record(keep, b'/m/%d' % i) for i in range(5)]
    plays = (C.c_uint * 1024).from_address(C.addressof(fw.var('st', C.c_char)) + scratch['plays'])
    st('plays_read', C.c_uint).value = 1
    for slot, (i, n) in enumerate(((1, 2), (3, 9), (0, 2))):  # most recently counted first
        plays[2 * slot], plays[2 * slot + 1] = listen_key(b'/m/%d' % i), n
    idx, score = (C.c_int * 100)(), (C.c_uint * 100)()
    top = fw.fn('library_lists__mp_rank', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p)(deques.new(songs), idx, score)
    assert top == 3 and list(idx[:3]) == [3, 1, 0]


def test_most_played_keeps_the_top_100(fw, st, scratch):
    keep, deques = [], Deques(fw)
    songs = [record(keep, b'/m/%d' % i) for i in range(150)]
    plays = (C.c_uint * 1024).from_address(C.addressof(fw.var('st', C.c_char)) + scratch['plays'])
    st('plays_read', C.c_uint).value = 1
    for i in range(150):
        plays[2 * i], plays[2 * i + 1] = listen_key(b'/m/%d' % i), i + 1
    idx, score = (C.c_int * 100)(), (C.c_uint * 100)()
    top = fw.fn('library_lists__mp_rank', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p)(deques.new(songs), idx, score)
    assert top == 100 and list(idx) == list(range(149, 49, -1))


# Ratings.
@pytest.fixture
def ratings(fw):
    files = Files(fw)
    keep = fw.fn('rate_keep', C.c_int, C.c_int, C.c_uint, C.c_int)
    kept = fw.fn('rate_kept', C.c_int, C.c_int, C.c_uint)
    return files, keep, kept


def test_a_rating_keeps_and_clears(ratings):
    files, keep, kept = ratings
    assert keep(0, 0x12345678, 3) == 1 and kept(0, 0x12345678) == 3
    assert keep(0, 0x12345678, 4) == 1 and kept(0, 0x12345678) == 4
    assert keep(0, 0x12345678, 0) == 1 and kept(0, 0x12345678) == 0
    assert keep(0, 0x12345678, 0) == 1  # nothing to forget


def test_five_hearts_are_my_favs_for_a_song_but_an_albums_own(ratings):
    files, keep, kept = ratings
    assert keep(0, 0x100, 5) == 1 and kept(0, 0x100) == 0
    assert keep(1, 0x100, 5) == 1 and kept(1, 0x100) == 5


def test_ratings_keep_songs_and_albums_apart(ratings):
    files, keep, kept = ratings
    keep(0, 0x100, 2), keep(1, 0x100, 3)
    assert kept(0, 0x100) == 2 and kept(1, 0x100) == 3
    assert set(files.files) == {'/mnt/data/ringnav-ratings', '/mnt/data/ringnav-album-ratings'}


def test_a_rating_not_saved_goes_back(ratings):
    files, keep, kept = ratings
    keep(0, 0x100, 2)
    files.fail_rename = True
    assert keep(0, 0x100, 4) == -1 and kept(0, 0x100) == 2


def test_a_malformed_slot_reads_as_no_rating(fw):
    Files(fw, {'/mnt/data/ringnav-ratings': struct.pack('<I', 0x100 | 7) + b'\0' * (O['RATE_SLOTS'] * 4 - 4)})
    assert fw.fn('rate_kept', C.c_int, C.c_int, C.c_uint)(0, 0x100) == 0


def test_a_full_ratings_table_refuses(fw):
    slots = O['RATE_SLOTS']
    Files(fw, {'/mnt/data/ringnav-ratings': b''.join(struct.pack('<I', (i + 1) << 3 | 1) for i in range(slots))})
    keep = fw.fn('rate_keep', C.c_int, C.c_int, C.c_uint, C.c_int)
    assert keep(0, 0xfffffff8, 2) == 0 and keep(0, 8, 2) == 1


# Era's Shuffle.
def test_era_shuffle_is_the_decades_songs_in_library_order(fw, heap):
    keep, deques = [], Deques(fw)
    state = fw.var('library_lists__era', Era)
    state.v, state.m, state.cap = C.cast(heap.calloc(2, C.sizeof(EraRow)), C.POINTER(EraRow)), 1, 1
    state.v[0].year = 1975
    songs = [record(keep, id_=i) for i in (5, 9, 1, 7, 3)]
    fw.mock('getAllMusic', lambda *_: deques[directory(fw)].extend(songs) or len(songs))
    def run(db, sql, callback, *_):
        if callback:
            call = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.POINTER(C.c_char_p), C.c_void_p)(callback)
            for key in (b'7', b'1', b'9'):
                call(None, 1, row(key), None)
    fw.mock('sqlite3_exec', run)
    out = fw.fn('library_lists__era_shuffle_songs', C.c_long)()
    assert [struct.unpack_from('<i', C.string_at(r, 8), O['REC_ID'])[0] for r in deques[out]] == [9, 1, 7]
    assert not fw.var('library_lists__era_keys', EraKeys).v


def test_era_shuffle_of_nothing(fw):
    deques = Deques(fw)
    assert deques[fw.fn('library_lists__era_shuffle_songs', C.c_long)()] == []
    assert not fw.calls('sqlite3_exec')


# Era.
class EraKeys(C.Structure):
    _fields_ = [('v', C.POINTER(C.c_uint)), ('n', C.c_uint), ('cap', C.c_uint)]


class EraRow(C.Structure):
    _fields_ = [('album', C.c_void_p), ('artist', C.c_void_p), ('path', C.c_void_p), ('year', C.c_int)]


class Era(C.Structure):
    _fields_ = [('page', C.c_void_p), ('albums', C.c_void_p), ('decade', C.c_int * 32), ('count', C.c_int * 32), ('n', C.c_int),
                ('v', C.POINTER(EraRow)), ('m', C.c_uint), ('cap', C.c_uint)]


def row(*values):
    return (C.c_char_p * len(values))(*values)


ROW = (C.c_int, C.c_void_p, C.c_int, C.POINTER(C.c_char_p), C.c_void_p)


@pytest.fixture
def era(fw, heap):
    state = fw.var('library_lists__era', Era)
    state.v = C.cast(heap.calloc(4, C.sizeof(EraRow)), C.POINTER(EraRow))
    state.cap = 3
    return state


def test_era_decades_in_order(fw):
    state = fw.var('library_lists__era', Era)
    add = fw.fn('library_lists__era_decade_row', *ROW)
    assert add(None, 2, row(b'1970', b'4'), None) == 0 and add(None, 2, row(b'2000', b'11'), None) == 0
    assert state.n == 2 and list(state.decade[:2]) == [1970, 2000] and list(state.count[:2]) == [4, 11]


@pytest.mark.parametrize('n, values', [(1, (b'1970', b'4')), (3, (b'1970', b'4', b'x')), (2, (None, b'4')), (2, (b'1970', None))])
def test_era_ignores_a_bad_decade_row(fw, n, values):
    state = fw.var('library_lists__era', Era)
    assert fw.fn('library_lists__era_decade_row', *ROW)(None, n, row(*values), None) == 0 and state.n == 0


def test_era_keeps_at_most_32_decades(fw):
    state = fw.var('library_lists__era', Era)
    add = fw.fn('library_lists__era_decade_row', *ROW)
    for i in range(40):
        add(None, 2, row(b'%d' % (1000 + i), b'1'), None)
    assert state.n == 32 and state.decade[31] == 1031


def test_era_album_row_copies_its_strings(fw, era):
    add = fw.fn('library_lists__era_album_row', *ROW)
    album, artist, year, path = text(b'Album'), text(b'Band'), text(b'1975'), text(b'/m/a')
    fields = (C.c_char_p * 4)(C.cast(album, C.c_char_p), C.cast(artist, C.c_char_p), C.cast(year, C.c_char_p), C.cast(path, C.c_char_p))
    assert add(None, 4, fields, None) == 0
    album[0] = b'X'
    assert era.m == 1 and C.string_at(era.v[0].album) == b'Album' and C.string_at(era.v[0].artist) == b'Band'
    assert C.string_at(era.v[0].path) == b'/m/a' and era.v[0].year == 1975


def test_era_album_row_with_no_artist(fw, era):
    fw.fn('library_lists__era_album_row', *ROW)(None, 4, row(b'A', None, b'1975', b'/m'), None)
    assert C.string_at(era.v[0].artist) == b''


@pytest.mark.parametrize('n, values', [(3, (b'A', b'B', b'1')), (4, (None, b'B', b'1', b'/m')), (4, (b'A', b'B', None, b'/m')), (4, (b'A', b'B', b'1', None))])
def test_era_ignores_a_bad_album_row(fw, era, n, values):
    assert fw.fn('library_lists__era_album_row', *ROW)(None, n, row(*values), None) == 0 and era.m == 0


def test_era_any_row_text(fw, heap):
    state = fw.var('library_lists__era', Era)
    decade, album = fw.fn('library_lists__era_decade_row', *ROW), fw.fn('library_lists__era_album_row', *ROW)
    rng = random.Random(0xe4a)
    for data in hostile(rng, [b'1975', b'2000', b'-1', b'99999999999', b'12x'], 1500):
        state.v = C.cast(heap.calloc(2, C.sizeof(EraRow)), C.POINTER(EraRow))
        state.cap, state.n = 1, 0
        album(None, 4, row(data, data or None, data, data), None)
        decade(None, 2, row(data, data), None)
        assert state.m == 1 and C.string_at(state.v[0].album) == data and state.n == 1
        fw.fn('library_lists__era_free', None)()


def test_era_albums_stop_at_the_decades_count(fw, era):
    add = fw.fn('library_lists__era_album_row', *ROW)
    for i in range(6):
        assert add(None, 4, row(b'A', b'B', b'%d' % i, b'/m'), None) == 0
    assert era.m == 3 and era.v[2].year == 2


def test_era_free_then_empty(fw, era):
    add = fw.fn('library_lists__era_album_row', *ROW)
    add(None, 4, row(b'A', b'B', b'1', b'/m'), None)
    fw.fn('library_lists__era_free', None)()
    assert not era.v and era.m == 0 and era.cap == 0
    fw.fn('library_lists__era_free', None)()  # nothing left to free


def test_era_closed(fw, era):
    closed = fw.fn('library_lists__era_closed', C.c_int, C.c_void_p, C.c_void_p)
    era.page, era.albums = 0x10, 0x20
    assert closed(0x30, None) == 0 and era.page == 0x10 and era.albums == 0x20 and era.cap == 3
    closed(0x10, None)
    assert not era.page and era.albums == 0x20 and era.cap == 3
    closed(0x20, None)
    assert not era.albums and era.cap == 0 and not era.v


def test_era_keys_grow_and_keep_their_values(fw):
    keys = fw.var('library_lists__era_keys', EraKeys)
    add = fw.fn('library_lists__era_key_row', *ROW)
    for i in range(3000):
        assert add(None, 1, row(b'%d' % i), None) == 0
    assert keys.n == 3000 and keys.cap == 4096 and list(keys.v[:3000]) == list(range(3000))


@pytest.mark.parametrize('n, values', [(0, ()), (2, (b'1', b'2')), (1, (None,))])
def test_era_keys_ignore_a_bad_row(fw, n, values):
    keys = fw.var('library_lists__era_keys', EraKeys)
    assert fw.fn('library_lists__era_key_row', *ROW)(None, n, row(*values) if values else None, None) == 0 and keys.n == 0


@pytest.mark.parametrize('a, b, order', [(1, 2, -1), (2, 1, 1), (7, 7, 0), (0xffffffff, 1, 1), (1, 0x80000000, -1)])
def test_era_key_cmp_is_unsigned(fw, a, b, order):
    assert fw.fn('library_lists__era_key_cmp', C.c_int, C.POINTER(C.c_uint), C.POINTER(C.c_uint))(C.c_uint(a), C.c_uint(b)) == order


@pytest.mark.parametrize('decade, name', [(1960, b'60s'), (1990, b'90s'), (1900, b'00s'), (2000, b'2000s'), (2010, b'2010s')])
def test_era_name(fw, decade, name):
    out = C.create_string_buffer(16)
    assert fw.fn('library_lists__era_name', C.c_char_p, C.c_void_p, C.c_int)(out, decade) == name


# Playlists and SQL rows.
def playlist_delete(fw, sql_fail=(), confirm=10, row_=0, ids=(3,)):
    keep, deques = [], Deques(fw)
    sql = Sql(fw, fail=sql_fail)
    seen = []
    def navigate(name, *_):
        seen.append(C.string_at(name))
        return confirm if b'confirm' in seen[-1] else 0
    fw.mock('navigator_to_with_context', navigate)
    fw.mock('widget_get_prop_int', lambda *_: row_)
    fw.var('fw_ctx_p_deque_playlist', C.c_uint).value = deques.new([record(keep, id_=i) for i in ids])
    fw.fn('ringnav_playlist_delete', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    return sql, seen


def test_a_playlist_goes_with_its_songs(fw):
    sql, seen = playlist_delete(fw)
    assert b'delete from playlist where id=3;select changes();delete from playlist where id>100 and cd_num=3' in sql.log
    assert sql.log[-1] == b'commit' and seen == [b'dialog/confirminfo_dialog', b'dialog/msginfo_dialog']
    assert len(fw.calls('navigator_back')) == 1 and fw.var('g_delete_flag', C.c_ubyte).value == 1


BUSY = b'PRAGMA busy_timeout=500'  # playlists.c's pl_db: a write waits out one playlist_art read


def test_each_playlist_writer_waits_for_a_reader_before_its_write(fw):
    """Create and Delete set a busy timeout on their connection before the write, so a commit that
    meets the art worker's read waits for it instead of failing with SQLITE_BUSY."""
    sql, _ = playlist_delete(fw)
    assert sql.log[:2] == [BUSY, b'begin immediate']
    made, sql = create(fw, b'Mine', (0, 1, 3))
    assert made == 2 and sql.log[:2] == [BUSY, b'begin immediate']


@pytest.mark.parametrize('opened', [0, 14])
def test_stock_s_connection_waits_for_a_reader_once_open(fw, opened):
    """tools/build.py's hook in stock's lazy open (0x5c8d64): the pragma on the connection it opened, none
    when the open failed, and the open's result back to stock."""
    sql = Sql(fw)
    fw.mock('sqlite3_open', lambda path, db, *_: C.c_void_p.from_address(db).__setattr__('value', 0xdb) or opened)
    db = C.c_void_p()
    assert fw.fn('stock_db_open', C.c_int, C.c_char_p, C.c_void_p)(b'/mnt/data/database.db', C.byref(db)) == opened
    assert sql.log == ([] if opened else [BUSY]) and [c[1][0] for c in fw.calls('sqlite3_exec')] == ([] if opened else [0xdb])


def test_a_cancelled_delete_changes_nothing(fw):
    sql, seen = playlist_delete(fw, confirm=0)
    assert not sql.log and seen == [b'dialog/confirminfo_dialog']


def test_a_failed_delete_rolls_back_and_says_so(fw):
    sql, seen = playlist_delete(fw, sql_fail=(b'delete',))
    assert sql.log[-1] == b'rollback' and not fw.calls('navigator_back') and seen[-1] == b'dialog/msginfo_dialog'


@pytest.mark.parametrize('row_, ids', [(-1, (3,)), (1, (3,)), (0, (100,)), (0, (-1,))])
def test_a_row_that_is_no_playlist_is_left(fw, row_, ids):
    sql, seen = playlist_delete(fw, row_=row_, ids=ids)
    assert not sql.log and not seen


@pytest.mark.parametrize('n, value, stop', [(1, b'1', 0), (1, b'0', 1), (1, b'2', 1), (0, b'1', 1), (2, b'1', 1), (1, None, 1)])
def test_ple_changed_wants_exactly_one_row(fw, n, value, stop):
    assert fw.fn('playlists__ple_changed', *ROW)(None, n, row(value, value), None) == stop


def test_ple_number(fw):
    number = fw.fn('playlists__ple_number', *ROW)
    out = C.c_int(-1)
    assert number(C.addressof(out), 1, row(b'42'), None) == 0 and out.value == 42


@pytest.mark.parametrize('n, value', [(1, None), (2, b'1'), (0, b'1')])
def test_ple_number_refuses_a_bad_row(fw, n, value):
    out = C.c_int(-1)
    assert fw.fn('playlists__ple_number', *ROW)(C.addressof(out), n, row(value, value), None) == 1 and out.value == -1


def test_sql_numbers_any_text(fw):
    used = (C.c_ubyte * 100)()
    mark, number = fw.fn('playlists__playlist_used', *ROW), fw.fn('playlists__ple_number', *ROW)
    out, rng = C.c_int(), random.Random(0x11d)
    for data in hostile(rng, [b'0', b'99', b'100', b'-1', b'2147483648', b'4294967296'], 1500):
        assert mark(C.addressof(used), 1, row(data), None) == 0
        assert number(C.addressof(out), 1, row(data), None) == 0
    assert sum(used) <= 100


def test_playlist_used_marks_only_ids_it_has_room_for(fw):
    used = (C.c_ubyte * 102)()
    mark = fw.fn('playlists__playlist_used', *ROW)
    for value in (b'0', b'99', b'100', b'101', b'-1'):
        assert mark(C.addressof(used), 1, row(value), None) == 0
    assert [i for i in range(102) if used[i]] == [0, 99]


@pytest.mark.parametrize('n, value', [(1, None), (2, b'1')])
def test_playlist_used_stops_at_a_bad_row(fw, n, value):
    assert fw.fn('playlists__playlist_used', *ROW)(None, n, row(value, value), None) == 1


class Sql:
    """sqlite3_exec: the statements seen, each query's rows handed to its callback, and an error where asked."""
    def __init__(self, fw, ids=(), exists=0, fail=()):
        self.log, self.ids, self.exists, self.fail = [], ids, exists, fail
        fw.mock('sqlite3_exec', self.run)

    def run(self, db, sql, callback, ctx, *_):
        statement = C.string_at(sql)
        self.log.append(statement)
        if any(f in statement for f in self.fail):
            return 1
        rows = []
        if statement.startswith(b'select id from playlist'):
            rows = [b'%d' % i for i in self.ids]
        elif statement.startswith(b'select count'):
            rows = [b'%d' % self.exists]
        elif b'select changes()' in statement:
            rows = [b'1']
        call = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.POINTER(C.c_char_p), C.c_void_p)(callback) if callback else None
        for value in rows:  # sqlite3_exec drops the rows when there's no callback
            if call and call(ctx, 1, row(value), None):
                return 4
        return 0


def create(fw, name, ids=(), **kwargs):
    sql = Sql(fw, ids, **kwargs)
    return fw.fn('ringnav_playlist_create', C.c_int, C.c_void_p, C.c_char_p)(0x10, name), sql


def test_a_new_playlist_takes_the_first_free_id(fw):
    made, sql = create(fw, b'Mine', (0, 1, 3))
    assert made == 2 and sql.log[-1] == b'commit'
    assert any(b"insert into playlist values (2,'Mine'" in s for s in sql.log)


def test_a_playlist_name_is_quoted(fw):
    _, sql = create(fw, b"Rock 'n' Roll")
    assert any(b"filename='Rock ''n'' Roll'" in s for s in sql.log) and any(b"(0,'Rock ''n'' Roll'" in s for s in sql.log)


def test_a_name_all_quotes_fits_its_buffer(fw):
    made, sql = create(fw, b"'" * 63)
    assert made == 0 and any(b"'" * 128 in s for s in sql.log)


def test_a_long_name_is_cut_and_not_matched(fw):
    made, sql = create(fw, b'n' * 70)
    assert made == 0 and any(b"(0,'" + b'n' * 63 + b"'" in s for s in sql.log)
    assert not any(s.startswith(b'select count') for s in sql.log)


def test_no_name_is_playlist_and_its_id(fw):
    made, sql = create(fw, b'', (0,))
    assert made == 1 and any(b"(1,'Playlist1'" in s for s in sql.log)


def test_a_taken_name_is_refused(fw):
    made, sql = create(fw, b'Mine', exists=1)
    assert made == -2 and sql.log[-1] == b'rollback'


def test_every_id_taken_is_refused(fw):
    made, sql = create(fw, b'Mine', range(100))
    assert made == -1 and sql.log[-1] == b'rollback'


def test_a_failed_insert_says_so(fw):
    made, sql = create(fw, b'Mine', fail=(b'insert',))
    assert made == -1 and sql.log[-1] == b'rollback'


def test_no_list_is_refused(fw):
    assert fw.fn('ringnav_playlist_create', C.c_int, C.c_void_p, C.c_char_p)(None, b'x') == -1


@pytest.mark.parametrize('dest, a, b', [(-1, 200, 0), (100, 200, 0), (1, 100, 0), (1, 200, 100), (1, 200, 200)])
def test_ple_mutate_refuses_before_the_database(fw, dest, a, b):
    keep = []
    def rec(i):
        r = C.create_string_buffer(0x60)
        struct.pack_into('<i', r, O['REC_ID'], i)
        keep.append(r)
        return r
    assert fw.fn('playlists__ple_mutate', C.c_int, C.c_int, C.c_void_p, C.c_void_p)(dest, rec(a), rec(b) if b else None) == 0
    assert not fw.calls('sqlite3_open')


# The library's SQL.
class Query:
    def __init__(self, fw):
        self.seen = []
        for name in ('stock_dbquery_trampoline', 'stock_sorted_query_trampoline'):
            fw.mock(name, lambda db, sql, row_, sort, *_: self.seen.append((C.string_at(sql), sort)) and 0)
        fw.mock('stock_content_query_trampoline', lambda db, sql, row_, arg, sort, *_: self.seen.append((C.string_at(sql), sort)) and 0)
        self.run = fw.fn('ringnav_library_query', C.c_int, C.c_char_p, C.c_void_p, C.c_void_p, C.c_int)


AA = b"ifnull(nullif(albumsonger,''),songer)"


@pytest.mark.parametrize('sql, want', [
    (b'select albumsonger from t', b'select ' + AA + b' from t'),
    (b'a albumsonger b albumsonger c', b'a ' + AA + b' b ' + AA + b' c'),
    (b'select ifnull(albumsonger,1) from t', b'select ifnull(albumsonger,1) from t'),   # Coverflow keeps the tag
    (b'select * from songtable', b'select * from songtable'),
    (b'select * from playlist where id > 100 and cd_num=3', b'select * from playlist where id > 100 and cd_num=3 order by id asc'),
    (b'select * from playlist where id > 100 and cd_num=3 order by x', b'select * from playlist where id > 100 and cd_num=3 order by x'),
    (b'select * from playlist where id <= 100', b'select * from playlist where id <= 100 order by name collate nocase, id'),
])
def test_library_query_rewrites(fw, sql, want):
    query = Query(fw)
    assert query.run(b'db', sql, None, 7) == 0 and query.seen == [(want, 7)]


def test_library_query_of_no_sql_passes(fw):
    query = fw.fn('ringnav_library_query', C.c_int, C.c_char_p, C.c_void_p, C.c_void_p, C.c_int)
    query(b'db', None, None, 0)
    assert len(fw.calls('stock_dbquery_trampoline')) == 1


@pytest.mark.parametrize('name, trampoline', [('ringnav_sorted_query', 'stock_sorted_query_trampoline')])
def test_sorted_query_is_the_sorted_trampolines(fw, name, trampoline):
    fw.fn(name, C.c_int, C.c_char_p, C.c_void_p, C.c_void_p, C.c_int)(b'db', b'select 1', None, 2)
    assert len(fw.calls(trampoline)) == 1 and not fw.calls('stock_dbquery_trampoline')


def test_content_query_passes_its_arg_and_sort(fw):
    query = Query(fw)
    fw.fn('ringnav_content_query', C.c_int, C.c_char_p, C.c_void_p, C.c_void_p, C.c_void_p, C.c_int)(b'db', b'select 1', None, 0x33, 9)
    assert query.seen == [(b'select 1', 9)] and fw.calls('stock_content_query_trampoline')[0][1][3] == 0x33


@pytest.mark.parametrize('fill, runs', [(2047 - len(AA) - 1, True), (2047 - len(AA), False), (5000, False)])
def test_library_query_refuses_what_wont_fit(fw, fill, runs):
    query = Query(fw)
    sql = b'y' * fill + b' albumsonger'
    assert query.run(b'db', sql, None, 0) == (0 if runs else -1) and len(query.seen) == int(runs)


def test_library_query_any_text(fw):
    query, rng = Query(fw), random.Random(0x5e1)
    pieces = (b'albumsonger', b'select ', b' from ', b"'", b'x' * 300, b'%s', b'%d', b'ifnull(', b'ifnull(albumsonger')
    for _ in range(1200):
        sql = b''.join(rng.choice(pieces) for _ in range(rng.randrange(14)))
        query.seen.clear()
        got = query.run(b'db', sql, None, 0)
        want = sql if b'albumsonger' not in sql or b'ifnull(albumsonger' in sql else sql.replace(b'albumsonger', AA)
        if want == sql or len(want) < 2048:
            assert got == 0 and query.seen[0][0] == want
        else:
            assert got == -1 and not query.seen


# Small page state.
def test_qm_gone_clears_its_own_dialog_only(fw, st):
    gone = fw.fn('navigation__qm_gone', C.c_int, C.c_void_p, C.c_void_p)
    dialog = st('qm_dialog', C.c_void_p)
    dialog.value = 0x10
    assert gone(0x20, None) == 0 and dialog.value == 0x10
    assert gone(0x10, None) == 0 and not dialog.value


def test_qm_playlist_gone(fw, st):
    st('qm_playlist', C.c_void_p).value = 0x10
    assert fw.fn('qm_playlist_gone', C.c_int, C.c_void_p, C.c_void_p)(None, None) == 0
    assert not st('qm_playlist', C.c_void_p).value


def test_rate_panel_gone_with_its_window(fw, st):
    gone = fw.fn('ratings__rate_panel_gone', C.c_int, C.c_void_p, C.c_void_p)
    st('rate_on', C.c_void_p).value = 0x10
    gone(0x20, None)
    assert st('rate_on', C.c_void_p).value == 0x10
    gone(0x10, None)
    assert not st('rate_on', C.c_void_p).value


def test_artist_cf_gone_clears_what_it_held(fw, st):
    gone = fw.fn('navigation__artist_cf_gone', C.c_int, C.c_void_p, C.c_void_p)
    win, veil = st('art_cf_win', C.c_void_p), st('art_cf_veil', C.c_void_p)
    win.value, veil.value = 0x10, 0x20
    gone(0x30, None)
    assert (win.value, veil.value) == (0x10, 0x20)
    gone(0x10, None)
    assert not win.value and veil.value == 0x20
    gone(0x20, None)
    assert not veil.value


def test_qm_restore_puts_back_what_a_go_to_overwrote(fw, st, scratch):
    saved = (C.c_ubyte * 912).from_address(C.addressof(fw.var('st', C.c_char)) + scratch['qm_classinfo'])
    saved[:912] = [i & 255 for i in range(912)]
    assert fw.fn('navigation__qm_restore', C.c_int, C.c_void_p, C.c_void_p)(None, None) == 0
    assert C.string_at(C.addressof(fw.var('fw_ctx_g_local_classinfo_save', C.c_char)), 912) == bytes(saved)


# Shared duration cache coverage lives in test_durations.py.


# Scrobbling.
class Sa(C.Structure):
    _fields_ = [('pending', C.c_uint), ('first_at', C.c_uint), ('check_at', C.c_uint), ('running', C.c_int),
                ('woke', C.c_int)]


MINUTE = 60000


@pytest.mark.parametrize('pending, minutes', [(0, 180), (1, 176), (10, 140), (29, 64), (30, 60), (31, 60), (1000, 60)])
def test_scrobble_wait_shrinks_with_listens(fw, pending, minutes):
    assert fw.fn('scrobble_auto_ms', C.c_uint, C.c_uint)(pending) == minutes * MINUTE


@pytest.mark.parametrize('now', [0, 1000, 0xffffff00, 40 * MINUTE])
@pytest.mark.parametrize('pending', [1, 40])
def test_a_failed_upload_is_due_again_in_30_minutes(fw, now, pending):
    sa = fw.var('scrobble_ui__sa', Sa)
    sa.pending = pending
    fw.fn('scrobble_ui__sa_retry', None, C.c_uint)(now)
    wait = fw.fn('scrobble_auto_ms', C.c_uint, C.c_uint)(pending)
    assert ((now + 30 * MINUTE - sa.first_at) & 0xffffffff) == wait
    assert ((now + 30 * MINUTE - 1 - sa.first_at) & 0xffffffff) < wait


def test_a_retry_comes_sooner_with_more_listens(fw):
    sa = fw.var('scrobble_ui__sa', Sa)
    sa.pending = 1
    fw.fn('scrobble_ui__sa_retry', None, C.c_uint)(1000000000)
    sa.pending = 2
    assert (1000000000 + 26 * MINUTE - sa.first_at) & 0xffffffff == fw.fn('scrobble_auto_ms', C.c_uint, C.c_uint)(2)


def auto(fw, now, wifi=1, flag=b'', page=0):
    fw.mock('time_now_ms', lambda *_: now)
    fw.mock('get_wifisignal', lambda *_: wifi)
    fw.mock('navigator_window_is_exist', lambda *_: page)
    Config(fw, AUTO=flag)
    fw.fn('scrobble_auto', None)()


def test_no_listens_no_upload(fw):
    auto(fw, 10 * 3600000)
    assert not fw.calls('get_wifisignal')


def test_a_listen_waits_three_hours(fw):
    sa = fw.var('scrobble_ui__sa', Sa)
    fw.mock('time_now_ms', lambda *_: 5000)
    fw.fn('scrobble_listened', None)()
    fw.fn('scrobble_listened', None)()
    assert sa.pending == 2 and sa.first_at == 5000
    auto(fw, 5000 + 172 * MINUTE - 1)
    assert not fw.calls('get_wifisignal')
    auto(fw, 5000 + 172 * MINUTE)
    assert len(fw.calls('get_wifisignal')) == 1


def test_no_wifi_checks_once_a_minute(fw):
    sa = fw.var('scrobble_ui__sa', Sa)
    sa.pending, sa.first_at = 1, 0
    now = 200 * MINUTE
    auto(fw, now, wifi=0)
    auto(fw, now + MINUTE - 1, wifi=0)
    assert len(fw.calls('get_wifisignal')) == 1
    auto(fw, now + MINUTE, wifi=0)
    assert len(fw.calls('get_wifisignal')) == 2


def test_with_auto_off_it_asks_again_in_30_minutes(fw):
    sa = fw.var('scrobble_ui__sa', Sa)
    sa.pending, sa.first_at = 1, 0
    now = 200 * MINUTE
    auto(fw, now)
    assert ((now + 30 * MINUTE - sa.first_at) & 0xffffffff) == 176 * MINUTE


def test_not_while_a_computer_has_the_card(fw):
    sa = fw.var('scrobble_ui__sa', Sa)
    sa.pending, sa.first_at = 1, 0
    auto(fw, 200 * MINUTE, page=1)
    assert sa.first_at == 0 and sa.running == 0


@pytest.mark.parametrize('data, value', [(b'\x78\x56\x34\x12', 0x12345678), (b'\xff\xff\xff\xff', 0xffffffff), (b'\0\0\0\x80', 0x80000000), (b'\x01\0\0\0', 1)])
def test_le32(fw, data, value):
    assert fw.fn('xiph__le32', C.c_uint, C.c_char_p)(data) == value


# Keys.
def test_hold_info_gives_play_and_return_700_ms(fw):
    info = fw.fn('ringnav_hold_info', C.POINTER(C.c_int), C.c_void_p, C.c_int)
    assert list(info(None, KEY_PLAY)[:2]) == [KEY_PLAY, 700] and list(info(None, KEY_RETURN)[:2]) == [KEY_RETURN, 700]


@in_fork
def test_hold_info_of_any_other_key_is_stocks(fw):
    log = (C.c_long * 3)()
    stock_stub(0x6b384c, 0x1234, log)
    assert fw.fn('ringnav_hold_info', C.c_void_p, C.c_void_p, C.c_int)(0x55, KEY_CENTER) == 0x1234
    assert list(log[:2]) == [0x55, KEY_CENTER]


@pytest.mark.parametrize('key, ms', [(KEY_PLAY, 700), (KEY_RETURN, 700), (O['KEY_PREV'], 1000)])
def test_hold_timer(fw, key, ms):
    fw.mock('timer_add', lambda *_: 31)
    assert fw.fn('ringnav_hold_timer', C.c_uint, C.c_void_p, C.c_void_p, C.c_uint, C.c_int)(None, None, 1000, key) == 31
    assert fw.calls('timer_add')[0][1][2] == ms


def test_a_centre_hold_can_be_ended(fw, st):
    fw.mock('timer_add', lambda *_: 31)
    fw.fn('ringnav_hold_timer', C.c_uint, C.c_void_p, C.c_void_p, C.c_uint, C.c_int)(None, None, 1000, KEY_CENTER)
    assert st('centre_hold', C.c_uint).value == 31


def test_a_passing_dark_press_clears_the_resume_guard(fw, st):
    guard = O['BOOT_KEY_GUARD']
    claim(guard)
    saved = C.string_at(guard, 1)
    st('dark').value = 1
    try:
        C.memset(guard, 1, 1)
        fw.fn('navigation__woke', None, C.c_uint)(5000)
        cleared = C.string_at(guard, 1)
    finally:
        C.memmove(guard, saved, 1)
    assert cleared == b'\0' and st('woke_at', C.c_uint).value == 5000 and st('dark').value == 3


class Posted(C.Structure):
    _fields_ = [('key', C.c_uint), ('from_', C.c_uint), ('to', C.c_uint)]


def key_event(key, at):
    event = C.create_string_buffer(0x40)
    struct.pack_into('<I', event, O['EVENT_KEY'], key)
    struct.pack_into('<I', event, O['EVENT_TIME'], at & 0xffffffff)
    return event


@pytest.fixture
def post(fw):
    now = {'t': 1000}
    def stamp(*_):
        now['t'] += 1
        return now['t']
    fw.mock('time_now_ms', stamp)
    run = fw.fn('ringnav_post_key', C.c_int, C.c_void_p, C.c_int, C.c_int)
    return lambda key: run(None, 1, key)


def remote_key(fw, key, at):
    return fw.fn('navigation__remote_key', C.c_int, C.c_void_p)(key_event(key, at))


def test_a_remote_key_is_known_by_its_stamp(fw, post):
    post(KEY_PLAY)  # posted from 1001 to 1002
    assert remote_key(fw, KEY_PLAY, 1001) == 1


@pytest.mark.parametrize('at', [1000, 1003])
def test_a_key_outside_the_post_is_the_wheels_own(fw, post, at):
    post(KEY_PLAY)
    assert remote_key(fw, KEY_PLAY, at) == 0


def test_another_key_is_not_the_remotes(fw, post):
    post(KEY_PLAY)
    assert remote_key(fw, KEY_RETURN, 1001) == 0


def test_a_remote_key_counts_once(fw, post):
    post(KEY_PLAY)
    assert remote_key(fw, KEY_PLAY, 1001) == 1 and remote_key(fw, KEY_PLAY, 1001) == 0


def test_a_press_and_its_release_are_both_known(fw, post):
    post(KEY_PLAY), post(KEY_PLAY)
    assert remote_key(fw, KEY_PLAY, 1001) == 1 and remote_key(fw, KEY_PLAY, 1003) == 1


def test_the_key_is_its_low_byte(fw, post):
    post(0x100 | KEY_PLAY)
    assert remote_key(fw, KEY_PLAY, 1001) == 1


def test_the_oldest_of_five_posts_is_gone(fw, post):
    for key in (1, 2, 3, 4, 5):
        post(key)
    assert remote_key(fw, 1, 1001) == 0 and remote_key(fw, 5, 1009) == 1


def test_a_post_across_the_clocks_wrap(fw):
    now = iter((0xffffffff, 1))
    fw.mock('time_now_ms', lambda *_: next(now))
    fw.fn('ringnav_post_key', C.c_int, C.c_void_p, C.c_int, C.c_int)(None, 1, KEY_PLAY)
    assert remote_key(fw, KEY_PLAY, 0) == 1


# Widgets, by the few words the C reads.
def event(kind, key):
    e = C.create_string_buffer(0x40)
    struct.pack_into('<i', e, O['EVENT_TYPE'], kind)
    struct.pack_into('<i', e, O['EVENT_KEY'], key)
    return e


DOWN, UP, LONG = O['EVT_KEY_DOWN'], O['EVT_KEY_UP'], O['EVT_KEY_DOWN'] + 1


class Gate:
    """chord_gate and wake_gate, fed events at the time of the clock."""
    def __init__(self, fw, name):
        self.now = 100000
        fw.mock('time_now_ms', lambda *_: self.now)
        self.gate = fw.fn(f'navigation__{name}', C.c_int, C.c_void_p)

    def __call__(self, kind, key, after=0):
        self.now += after
        return self.gate(event(kind, key))


def test_centre_passes_alone(fw):
    gate = Gate(fw, 'chord_gate')
    assert (gate(DOWN, KEY_CENTER), gate(UP, KEY_CENTER, 100)) == (0, 0)


def test_centre_on_a_held_edge_button_is_dropped_whole(fw):
    gate = Gate(fw, 'chord_gate')
    assert gate(DOWN, KEY_RETURN) == 0
    assert gate(DOWN, KEY_CENTER, 100) == 1 and gate(DOWN, KEY_CENTER, 200) == 1 and gate(UP, KEY_CENTER, 100) == 1
    assert gate(UP, KEY_RETURN, 10) == 0


def test_centre_late_on_a_held_button_passes(fw):
    gate = Gate(fw, 'chord_gate')
    gate(DOWN, KEY_PLAY)
    assert gate(DOWN, KEY_CENTER, 500) == 0


def test_a_released_edge_button_drops_nothing(fw):
    gate = Gate(fw, 'chord_gate')
    gate(DOWN, KEY_PLAY), gate(UP, KEY_PLAY, 50)
    assert gate(DOWN, KEY_CENTER, 10) == 0


def test_an_edge_press_ends_centres_hold(fw, st):
    gate = Gate(fw, 'chord_gate')
    st('centre_hold', C.c_uint).value = 77
    gate(DOWN, KEY_CENTER)
    assert gate(DOWN, KEY_PLAY, 499) == 0
    assert [c[1][0] for c in fw.calls('timer_remove')] == [77] and st('centre_hold', C.c_uint).value == 0


def test_a_late_edge_press_leaves_centres_hold(fw, st):
    gate = Gate(fw, 'chord_gate')
    st('centre_hold', C.c_uint).value = 77
    gate(DOWN, KEY_CENTER)
    gate(DOWN, KEY_PLAY, 501)  # its press is stamped now | 1
    assert not fw.calls('timer_remove') and st('centre_hold', C.c_uint).value == 77


def test_a_ghost_after_return_held_is_dropped_whole(fw):
    gate = Gate(fw, 'chord_gate')
    gate(DOWN, KEY_RETURN), gate(UP, KEY_RETURN, 700)
    assert gate(DOWN, KEY_RETURN, 134) == 1 and gate(UP, KEY_RETURN, 20) == 1
    assert gate(DOWN, KEY_RETURN, 20) == 0


def test_a_return_press_after_the_ghost_window_passes(fw):
    gate = Gate(fw, 'chord_gate')
    gate(DOWN, KEY_RETURN), gate(UP, KEY_RETURN, 700)
    assert gate(DOWN, KEY_RETURN, 136) == 0  # the release is stamped now | 1


def test_a_short_return_leaves_no_ghost(fw):
    gate = Gate(fw, 'chord_gate')
    gate(DOWN, KEY_RETURN), gate(UP, KEY_RETURN, 100)
    assert gate(DOWN, KEY_RETURN, 10) == 0


@pytest.mark.parametrize('kind', [0x100, 0x10c])
def test_other_events_pass_every_gate(fw, kind):
    assert Gate(fw, 'chord_gate')(kind, KEY_CENTER) == 0 and Gate(fw, 'wake_gate')(kind, KEY_RETURN) == 0


@pytest.fixture
def guard():
    address = O['BOOT_KEY_GUARD']
    claim(address)
    saved = C.string_at(address, 1)
    C.memset(address, 0, 1)
    yield address
    C.memmove(address, saved, 1)


def test_a_lit_centre_press_passes(fw, st, guard):
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    gate = Gate(fw, 'wake_gate')
    assert (gate(DOWN, KEY_CENTER), gate(UP, KEY_CENTER, 100)) == (0, 0)


def test_return_stays_dark(fw, st, guard):
    gate = Gate(fw, 'wake_gate')
    assert gate(DOWN, KEY_RETURN) == 1 and gate(LONG, KEY_RETURN) == 1 and gate(UP, KEY_RETURN) == 1


def test_other_keys_work_dark(fw, st, guard):
    assert Gate(fw, 'wake_gate')(DOWN, KEY_PLAY) == 0


def test_a_dark_press_wakes_and_a_blind_second_is_dropped_with_its_release(fw, st, guard):
    gate = Gate(fw, 'wake_gate')
    assert gate(DOWN, KEY_CENTER) == 0 and st('dark').value & 2 and gate(UP, KEY_CENTER, 50) == 0
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    assert gate(DOWN, KEY_CENTER, 100) == 1 and gate(DOWN, KEY_CENTER, 100) == 1 and gate(UP, KEY_CENTER, 100) == 1
    assert gate(DOWN, KEY_CENTER, 5000) == 0


def test_double_press_wake_drops_a_lone_press_whole(fw, st, guard):
    st('wake_double').value = 1
    gate = Gate(fw, 'wake_gate')
    assert gate(DOWN, KEY_CENTER) == 1 and gate(UP, KEY_CENTER, 50) == 1
    assert not st('dark').value & 2


def test_double_press_wake_passes_the_second(fw, st, guard):
    st('wake_double').value = 1
    gate = Gate(fw, 'wake_gate')
    gate(DOWN, KEY_CENTER), gate(UP, KEY_CENTER, 50)
    assert gate(DOWN, KEY_CENTER, 100) == 0 and st('dark').value & 2 and gate(UP, KEY_CENTER, 50) == 0


def test_a_standby_resume_guard_is_consumed_by_a_press(fw, st, guard):
    C.memset(guard, 1, 1)
    gate = Gate(fw, 'wake_gate')
    gate(DOWN, KEY_CENTER)
    assert C.string_at(guard, 1) == b'\0'


def input_event(kind, key, at):
    e = event(kind, key)
    struct.pack_into('<I', e, O['EVENT_TIME'], at)
    return e


def test_a_remote_key_passes_the_buttons_lock(fw, post, guard):
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    lock, seen = fw.var('g_keylock_flag', C.c_ubyte), []
    lock.value = 1
    fw.mock('stock_input_trampoline', lambda wm, e, *_: seen.append(lock.value) and 0)
    post(KEY_PLAY)
    send = fw.fn('ringnav_input', C.c_int, C.c_void_p, C.c_void_p)
    send(None, input_event(DOWN, KEY_PLAY, 1001))
    assert seen == [0] and lock.value == 1
    send(None, input_event(DOWN, KEY_PLAY, 5000))
    assert seen == [0, 1]


def test_a_dropped_key_never_reaches_stock(fw, guard):
    fw.mock('time_now_ms', lambda *_: 1000)
    fw.fn('ringnav_input', C.c_int, C.c_void_p, C.c_void_p)(None, input_event(DOWN, KEY_RETURN, 1000))
    assert not fw.calls('stock_input_trampoline')


def test_tile_is_under_half_a_row(fw):
    tile = fw.fn('navigation__tile_in', C.c_int, C.c_void_p, C.c_void_p)
    button, row_ = C.create_string_buffer(0x20), C.create_string_buffer(0x20)
    struct.pack_into('<i', row_, O['W_W'], 100)
    results = []
    for width in (49, 50, 51):
        struct.pack_into('<i', button, O['W_W'], width)
        results.append(tile(button, row_))
    assert results == [1, 0, 0]


def widgets(*parents):
    """Widgets chained child to parent: the last has none. Returns their addresses, kept alive with them."""
    held = [C.create_string_buffer(0x60) for _ in parents]
    for child, parent in zip(held, held[1:]):
        struct.pack_into('<Q', child, O['W_PARENT'], C.addressof(parent))
    return held, [C.addressof(w) for w in held]


@pytest.mark.parametrize('painting, row_, sel, inside', [(0, 2, None, 1), (0, 0, None, 1), (0, 3, None, 0), (0, 2, 1, 0), (0, 1, 1, 1), (None, 1, None, 0)])
def test_painting_in_walks_up_from_the_widget_being_painted(fw, st, painting, row_, sel, inside):
    held, at = widgets(0, 1, 2)
    st('paint_w', C.c_void_p).value = None if painting is None else at[painting]
    st('sel_w', C.c_void_p).value = None if sel is None else at[sel]
    target = at[row_] if row_ < 3 else 0x1234
    assert fw.fn('painting_in', C.c_int, C.c_void_p)(target) == inside and held


def test_painting_in_nothing(fw, st):
    held, at = widgets(0, 1)
    st('paint_w', C.c_void_p).value = at[0]
    assert fw.fn('painting_in', C.c_int, C.c_void_p)(None) == 0 and held


def test_selected_ink_is_the_selected_widgets(fw, st):
    held, at = widgets(0, 1)
    st('paint_w', C.c_void_p).value = at[0]
    st('sel_widget', C.c_void_p).value = at[1]
    assert fw.fn('navigation__selected_ink', C.c_int)() == 1 and held


def test_artist_songs_by_the_artist_setting(fw):
    fw.var('fw_ctx_artist_type', C.c_int).value = 1
    assert fw.fn('navigation__artist_songs', C.c_int, C.c_void_p)(None) == 0
    assert fw.calls('getMusicByAlbumArtist')[0][1][0] == C.addressof(fw.var('fw_ctx_g_local_classinfo_save', C.c_char)) + 0x10d
    fw.var('fw_ctx_artist_type', C.c_int).value = 0
    fw.fn('navigation__artist_songs', C.c_int, C.c_void_p)(None)
    assert len(fw.calls('getMusicBySonger')) == 1


def test_artist_songs_of_the_unknown_artist(fw):
    fw.var('fw_ctx_g_local_classinfo_save', C.c_char * host.tools_build.CONTEXT_DATA['g_local_classinfo_save'])[0xa] = b'\1'
    fw.fn('navigation__artist_songs', C.c_int, C.c_void_p)(None)
    assert fw.calls('getMusicBySonger')[0][1][0] == 0


def test_a_badge_is_stocks_none(fw):
    out = C.create_string_buffer(16)
    assert fw.fn('ringnav_badge', C.c_int, C.c_char_p, C.c_void_p, C.c_int)(b'/a.flac', out, 16) == 0 and out.value == b'null'


@pytest.mark.parametrize('path, videos', [(b'/mnt/mmc/Videos', 1), (b'/mnt/mmc/VIDEOS/x', 1), (b'/mnt/mmc/Videosx', 0), (b'/mnt/mmc/Music', 0), (b'/mnt/usb/Videos', 0)])
def test_folder_videos(fw, path, videos):
    strings(fw)
    C.memmove(C.addressof(fw.var('fw_ctx_g_folder_path', C.c_char)), path + b'\0', len(path) + 1)
    assert fw.fn('folder_videos', C.c_int)() == videos


@pytest.mark.parametrize('table, exists', [(None, 1), (b'playlist', 1), (b'songtable', 0), (b'playlist', 0)])
def test_scan_refresh_early_answers(fw, table, exists):
    """Another table's row, or a song the library doesn't have yet (stock adds it): stock's answer, nothing refreshed."""
    fw.mock('toolsCheckItemExist', lambda *_: exists)
    assert fw.fn('ringnav_scan_refresh', C.c_int, C.c_void_p, C.c_char_p, C.c_int)(None, table, 0) == exists
    assert not fw.calls('toolsUpdateDbTable')


def test_scan_refresh_keeps_a_row_without_a_path(fw):
    fw.mock('toolsCheckItemExist', lambda *_: 1)
    assert fw.fn('ringnav_scan_refresh', C.c_int, C.c_void_p, C.c_char_p, C.c_int)(C.create_string_buffer(0x60), b'songtable', 0) == 1
    assert not fw.calls('toolsUpdateDbTable')



@pytest.mark.parametrize('resume', [b'0', b'1'])
def test_a_headsets_link_starts_nothing_over_a_card_game_with_the_sound(fw, resume):
    """checkBluetoothStatus' restart once A2DP is up, Resume on Connect either way; without the game it restarts."""
    Config(fw, RESUME=resume)
    fw.mock('mclGetSavePlayStatus', lambda *_: 2)
    start = fw.fn('ringnav_bt_connect_start', C.c_int, C.c_int)
    gm = fw.var('addon__gm', Game)
    gm.pid, gm.audio = 4321, 1
    assert start(0) == 0 and not fw.calls('player_save_start')
    gm.pid = 0
    start(0)
    assert len(fw.calls('player_save_start')) == 1


def test_a_card_game_with_the_sound_fails_every_start_of_stocks_player(fw):
    """mclStartPlayer's hook: a jack or USB DAC change, a Bluetooth drop, a card app's stream all start here; -1 as a too-long track."""
    start = fw.fn('ringnav_start_player', C.c_int)
    gm = fw.var('addon__gm', Game)
    gm.pid, gm.audio = 4321, 1
    assert start() == -1 and not fw.calls('stock_start_player_trampoline')
    gm.audio = 0  # a game without the sound: stock's start runs
    start()
    assert len(fw.calls('stock_start_player_trampoline')) == 1
