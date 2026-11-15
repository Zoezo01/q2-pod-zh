"""patch/navigation.c's paint hooks and Classic's own screens: the image hook (accent, Light Mode, Minimal's grey
icons, Classic's marks and tiles, art corners), the style colour and gradient hooks, the background hook, the
bar title and Quick Settings, with volume.c's volume overlay and classic_pages.c's Search, playlist box, sliders
and message screens."""
import collections
import ctypes as C
import re
import struct

import pytest

import host
from conftest import O, Files, block, in_fork

W_X, W_Y, W_W, W_H, W_PARENT = O['W_X'], O['W_Y'], O['W_W'], O['W_H'], O['W_PARENT']
CANVAS_LCD, LCD_FILL_COLOR = O['CANVAS_LCD'], O['LCD_FILL_COLOR']
EVT_DESTROY = O['EVT_DESTROY']
STOCK_RED, PAPER, INK = O['STOCK_RED'], O['LIGHT_PAPER'], O['LIGHT_INK']
RET_OK, RET_REMOVE, RET_REPEAT = 0, 7, 8
OFFSETS = (host.ROOT/'patch/offsets.inc').read_text()
STRINGS = dict(re.findall(r'^#define (\w+) +"([^"]*)"', OFFSETS, re.M))
ACCENTS = [[int(v, 16) for v in re.findall(r'0x[0-9a-f]+', row)] for row in re.findall(r'\{ (0x[^}]*)\}', OFFSETS)][:9]
GRAPHITE, CRIMSON, TIDAL, IVORY = 0, 1, 2, 7
LIGHT, RED = 2, 3  # ACCENTS' columns: the light tone, the red tone
MINIMAL, CLASSIC, MODERN = '0', '1', '2'
CLASSIC_BLUE = 9  # Classic's own scheme, after the nine accents
# bitmap_t's formats (u16 @0xe) and where each keeps r, g, b and a (docs/internals.md#accent)
AT = {1: (0, 1, 2, 3), 2: (3, 2, 1, 0), 3: (2, 1, 0, 3), 4: (1, 2, 3, 0)}
OPAQUE, PREMULTI = 1, O['BITMAP_PREMULTI']
# theme_t.font_px[]'s roles, in theme.h's order
_roles = re.search(r'enum \{ (ROLE_\w+(?:, ROLE_\w+)*) \}', (host.ROOT/'patch/theme.h').read_text())
assert _roles
ROLE_BAR = _roles[1].split(', ').index('ROLE_BAR')
# Classic's own geometry set (its theme.xml), which tools/ipod.py bakes into stock's rows
_set = re.search(r'<geometry name="classic-light" ([^>]*)/>', (host.ROOT/'assets/themes/classic-light/theme.xml').read_text())
assert _set
GEOMETRY = {k: int(v) for k, v in re.findall(r'(\w+)="(\d+)"', _set[1])}


def rgba(c, a=0xff):
    """0xRRGGBB as a color_t, whose bytes are r, g, b, a."""
    return a << 24 | (c & 255) << 16 | c & 0xff00 | c >> 16


def rgb_of(color):
    """A color_t's 0xRRGGBB."""
    return (color & 255) << 16 | color & 0xff00 | color >> 16 & 255


def channels(c):
    return c >> 16 & 255, c >> 8 & 255, c & 255


def luma(c):
    r, g, b = channels(c)
    return (r * 299 + g * 587 + b * 114) // 1000


class Config:
    """config.ini, its value or the default."""
    def __init__(self, fw, **values):
        self.values = {k.encode(): v.encode() for k, v in values.items()}
        fw.mock('toolsReadConfig', self.read)

    def read(self, path, section, key, out, default, *_):
        value = self.values.get(C.string_at(key), C.string_at(default))
        C.memmove(out, value + b'\0', len(value) + 1)


def strings(fw):
    """Stock's AWTK string tests, which the C leans on."""
    fw.mock('tk_str_start_with', lambda s, prefix, *_: C.string_at(s).startswith(C.string_at(prefix)))
    fw.mock('tk_str_end_with', lambda s, end, *_: C.string_at(s).endswith(C.string_at(end)))


def theme(fw, number=MODERN, **values):
    """The settings the C reads at its first colour: a theme, its accent and the rest."""
    strings(fw)
    Config(fw, THEME=number, **values)


@pytest.fixture(scope='module')
def layout(library):
    """scratch_t's, scheme_t's and theme_t's field offsets as this build lays them out (their pointers move them), and
    volume.c's own state's, vd."""
    obj = library.parent/'navigation.o'
    return {'st': host.offsets(obj, 'scratch_t'), 'scheme': host.offsets(obj, 'scheme_t'),
            'theme': host.offsets(obj, 'theme_t'), 'vd': host.offsets(library.parent/'volume.o', 'vd')}


@pytest.fixture
def st(fw, layout):
    base = C.addressof(fw.var('st', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + layout['st'][name])


@pytest.fixture
def vd(fw, layout):
    """volume.c's own state by field: the dialog vol_paint last drew, its value and the poll's timer."""
    base = C.addressof(fw.var('volume__vd', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + layout['vd'][name])


@pytest.fixture
def scheme(fw, layout):
    """The live scheme's 0xRRGGBB fields by name, as the theme declares them for the half on show."""
    def read(name):
        return C.c_uint.from_address(fw.fn('scheme', C.c_void_p)() + layout['scheme'][name]).value
    return read


@pytest.fixture
def theme_t(fw, layout):
    """The live theme's fields by name, as tools/theme.py wrote them from its theme.xml: an array's index-th."""
    def read(name, ctype, index=0):
        return ctype.from_address(fw.fn('theme', C.c_void_p)() + layout['theme'][name] + index * C.sizeof(ctype)).value
    return read


@pytest.fixture(scope='module')
def stock_h(library):
    """The build's stock.h string lists: tools/build.py writes them from ipod.json and tools/art.py."""
    found = dict(re.findall(r'^#define (\w+) "(.*)"$', (library.parent/'stock.h').read_text(), re.M))
    return {k: [n for n in v.split('\\0') if n] for k, v in found.items()}


# The image hook: theme images as they are decoded, before the image manager caches them.

class Bitmap:
    """A decoded bitmap_t: w @0, h @4, flags @0xc, format @0xe, and pixels of rows stride long, each padded with
    0x5a bytes the C must leave."""
    def __init__(self, fw, rows, fmt=1, pad=2, flags=0):
        self.h, self.w, self.at = len(rows), len(rows[0]), AT.get(fmt, AT[1])
        self.stride = 4 * (self.w + pad)
        self.header = block(fw, size=0x40)
        self.data = block(fw, size=self.stride * self.h)
        C.memmove(self.header, struct.pack('<iiiHH', self.w, self.h, 0, flags, fmt), 16)
        raw = bytearray(b'\x5a' * (self.stride * self.h))
        for y, row in enumerate(rows):
            for x, p in enumerate(row):
                for k in range(4):
                    raw[y * self.stride + 4 * x + self.at[k]] = p[k]
        C.memmove(self.data, bytes(raw), len(raw))

    def get(self, x, y):
        raw = C.string_at(self.data + y * self.stride + 4 * x, 4)
        return tuple(raw[self.at[k]] for k in range(4))

    def rows(self):
        raw = C.string_at(self.data, self.stride * self.h)
        return [[tuple(raw[y * self.stride + 4 * x + self.at[k]] for k in range(4)) for x in range(self.w)]
                for y in range(self.h)]

    def padding(self):
        return {C.string_at(self.data + y * self.stride + 4 * self.w, self.stride - 4 * self.w) for y in range(self.h)}

    @property
    def flags(self):
        return C.c_ushort.from_address(self.header + 0xc).value


class Bitmaps:
    """bitmap_lock_buffer_for_write and the rest over Bitmap's, by header."""
    def __init__(self, fw):
        self.known, self.locked = {}, []
        fw.mock('bitmap_lock_buffer_for_write', lambda bm, *_: self.locked.append(bm) or self.known[bm].data)
        fw.mock('bitmap_get_line_length', lambda bm, *_: self.known[bm].stride)

    def __call__(self, fw, rows, **kw):
        bm = Bitmap(fw, rows, **kw)
        self.known[bm.header] = bm
        return bm


@pytest.fixture
def bitmaps(fw):
    return Bitmaps(fw)


def fill(w, h, px):
    return [[px] * w for _ in range(h)]


def image_add(fw, name, bm, result=0):
    """The hook, stock's own add after it answering result: what reached stock."""
    seen = []
    fw.mock('stock_image_trampoline', lambda m, n, b, *_: seen.append((m, C.string_at(n) if n else None, b)) or result)
    got = fw.fn('ringnav_image_add', C.c_int, C.c_void_p, C.c_char_p, C.c_void_p)(0x5100, name, bm.header if bm else None)
    assert got == result and seen == [(0x5100, name, bm.header if bm else 0)]
    return seen


def px(c, a=255):
    return (*channels(c), a)


def saturation(p):
    return max(p[:3]) - min(p[:3])


RED_PX, WHITE, CLEAR = px(STOCK_RED), (255, 255, 255, 255), (0, 0, 0, 0)


@pytest.mark.parametrize('fmt', [1, 2, 3, 4])
@pytest.mark.parametrize('accent', [TIDAL, 4, 5])
def test_a_theme_image_s_red_takes_the_accent_s_red_tone_in_any_byte_order(fw, bitmaps, fmt, accent):
    theme(fw, MODERN, ACCENT=str(accent))
    bm = bitmaps(fw, [[RED_PX, px(0x808080), px(0x123456)]], fmt=fmt)
    image_add(fw, b'play_mark', bm)
    assert bm.rows() == [[px(ACCENTS[accent][RED]), px(0x808080), px(0x123456)]]  # greys and other hues stay
    assert bm.padding() == {b'\x5a' * 8}


def test_an_unknown_format_or_no_bitmap_is_left_to_stock(fw, bitmaps):
    theme(fw, MODERN, ACCENT=str(TIDAL))
    bm = bitmaps(fw, [[RED_PX]], fmt=5)
    image_add(fw, b'play_mark', bm)
    image_add(fw, b'play_mark', None, result=3)
    assert bitmaps.locked == [] and bm.get(0, 0) == RED_PX


def test_crimson_is_stock(fw, bitmaps):
    theme(fw, MODERN, ACCENT=str(CRIMSON))
    bm = bitmaps(fw, [[RED_PX, WHITE]])
    image_add(fw, b'play_mark', bm)
    assert bm.rows() == [[RED_PX, WHITE]]


@pytest.mark.parametrize('name', [b'file:///mnt/mmc/Music/a.jpg', b'/usr/share/x.png', b'http://host/a.png', b'menu_music'])
def test_covers_by_path_and_home_s_cards_keep_their_colours(fw, bitmaps, name):
    theme(fw, MODERN, ACCENT=str(TIDAL), LIGHT='1')
    bm = bitmaps(fw, [[RED_PX, WHITE]])
    image_add(fw, name, bm)
    assert bm.rows() == [[RED_PX, WHITE]]


def test_home_s_arrows_still_flip_in_light_mode(fw, bitmaps):
    theme(fw, MODERN, ACCENT=str(TIDAL), LIGHT='1')
    bm = bitmaps(fw, [[WHITE, CLEAR]])
    image_add(fw, b'menu_left', bm)
    assert bm.rows() == [[(INK, INK, INK, 255), CLEAR]]


def test_the_settings_icons_keep_their_category_red(fw, bitmaps, stock_h):
    theme(fw, MODERN, ACCENT=str(TIDAL))
    name = stock_h['SETTINGS_ICON_NAMES'][0].encode()
    bm = bitmaps(fw, [[RED_PX, WHITE]])
    image_add(fw, name, bm)
    assert bm.rows() == [[RED_PX, WHITE]]


def test_graphite_s_knob_red_takes_the_light_tone_and_its_tick_red_the_red_one(fw, bitmaps):
    """Only Graphite's light and red tones differ: red under an opaque white glyph is a surface (light tone),
    red alone a mark (red tone)."""
    theme(fw, MODERN, ACCENT=str(GRAPHITE))
    knob, tick = bitmaps(fw, [[RED_PX, WHITE]]), bitmaps(fw, [[RED_PX, CLEAR]])
    image_add(fw, b'switch_on', knob)
    image_add(fw, b'list_tick', tick)
    assert knob.rows() == [[px(ACCENTS[GRAPHITE][LIGHT]), WHITE]]
    assert tick.rows() == [[px(ACCENTS[GRAPHITE][RED]), CLEAR]]
    assert ACCENTS[GRAPHITE][LIGHT] != ACCENTS[GRAPHITE][RED]


def test_a_half_white_glyph_is_not_a_surface(fw, bitmaps):
    theme(fw, MODERN, ACCENT=str(GRAPHITE))
    bm = bitmaps(fw, [[RED_PX, (255, 255, 255, 128)]])
    image_add(fw, b'list_tick', bm)
    assert bm.get(0, 0) == px(ACCENTS[GRAPHITE][RED])


@pytest.mark.parametrize('accent', [CRIMSON, TIDAL, GRAPHITE])
def test_the_confirm_discs_take_the_dark_surface_under_every_accent(fw, bitmaps, accent):
    theme(fw, MODERN, ACCENT=str(accent))
    bm = bitmaps(fw, [[RED_PX, WHITE]])
    image_add(fw, STRINGS['CONFIRM_IMAGE'].encode() + b'ok', bm)
    assert bm.rows() == [[px(O['CONFIRM_SURFACE']), WHITE]]


@pytest.mark.parametrize('accent, glyph', [(GRAPHITE, O['CONFIRM_SURFACE']), (TIDAL, 0xffffff)])
def test_an_active_quick_control_s_glyph_turns_dark_on_a_bright_tone(fw, bitmaps, accent, glyph):
    theme(fw, MODERN, ACCENT=str(accent))
    bm = bitmaps(fw, [[RED_PX, WHITE]])
    image_add(fw, STRINGS['DROPDOWN_IMAGE'].encode() + b'wifi_on', bm)
    assert bm.rows() == [[px(ACCENTS[accent][RED]), px(glyph)]]


def test_an_inactive_quick_control_keeps_its_white_glyph(fw, bitmaps):
    theme(fw, MODERN, ACCENT=str(GRAPHITE))
    bm = bitmaps(fw, [[px(0x555555), WHITE]])
    image_add(fw, STRINGS['DROPDOWN_IMAGE'].encode() + b'wifi_off', bm)
    assert bm.rows() == [[px(0x555555), WHITE]]


def test_the_brightness_suns_are_not_controls(fw, bitmaps):
    """drop_light*: a sun with red under white is a surface like any other image, its glyph stays white."""
    theme(fw, MODERN, ACCENT=str(GRAPHITE))
    bm = bitmaps(fw, [[RED_PX, WHITE]])
    image_add(fw, STRINGS['DROPDOWN_SUN'].encode() + b't_on', bm)
    assert bm.rows() == [[px(ACCENTS[GRAPHITE][LIGHT]), WHITE]]


@pytest.mark.parametrize('fmt', [1, 3])
def test_minimal_greys_a_list_icon_s_disc_and_keeps_its_glyph(fw, bitmaps, scheme, fmt):
    """The disc (its most saturated pixels) turns the scheme's chevron grey, the white glyph stays, and an edge
    halfway between blends halfway."""
    theme(fw, MINIMAL, MINIMAL_ACCENT=str(TIDAL))
    half = tuple((v + 255) // 2 for v in RED_PX)  # stock red halfway to white: half the disc's saturation
    bm = bitmaps(fw, [[RED_PX, WHITE, half, px(STOCK_RED, 0x40)]], fmt=fmt)
    image_add(fw, b'list_music', bm)
    grey = scheme('chevron') & 255
    edge = 255 - (255 - grey) * saturation(half) // saturation(RED_PX)
    assert bm.rows() == [[(grey,) * 3 + (255,), WHITE, (edge,) * 3 + (255,), (grey,) * 3 + (0x40,)]]


def test_minimal_leaves_an_already_grey_icon(fw, bitmaps):
    theme(fw, MINIMAL, MINIMAL_ACCENT=str(TIDAL))
    bm = bitmaps(fw, [[px(0x333333), WHITE]])
    image_add(fw, b'list_music', bm)
    assert bm.rows() == [[px(0x333333), WHITE]]


@pytest.mark.parametrize('name', [b'list_into', b'bt_lefticon', b'bt_refresh', b'list_delete'])
def test_ivory_s_card_inks_the_list_marks_with_the_chevron(fw, bitmaps, scheme, name):
    """Under Minimal's own accent the selected row is an off-white card: its white marks would vanish."""
    theme(fw, MINIMAL, MINIMAL_ACCENT=str(IVORY))
    bm = bitmaps(fw, [[WHITE, (255, 255, 255, 0x80)]])
    image_add(fw, name, bm)
    grey = scheme('chevron') & 255
    assert bm.rows() == [[(grey,) * 3 + (255,), (grey,) * 3 + (0x80,)]]


def test_another_minimal_accent_keeps_the_list_marks_white(fw, bitmaps):
    theme(fw, MINIMAL, MINIMAL_ACCENT=str(TIDAL))
    bm = bitmaps(fw, [[WHITE]])
    image_add(fw, b'list_into', bm)
    assert bm.rows() == [[WHITE]]


def test_baidu_s_square_is_cut_to_a_disc(fw, bitmaps):
    """Cut to one as wide as the square, its edge soft at the square's sides."""
    theme(fw, MODERN, ACCENT=str(TIDAL))
    bm = bitmaps(fw, fill(12, 12, WHITE))
    image_add(fw, b'list_baidu', bm)
    alpha = [[p[3] for p in row] for row in bm.rows()]
    assert alpha[0][0] == alpha[0][11] == alpha[11][0] == alpha[11][11] == 0  # its corners gone
    assert alpha[5][5] == alpha[6][6] == 255 and 0 < alpha[6][0] < 255  # its middle kept, its edge soft
    assert alpha[6][6:] == sorted(alpha[6][6:], reverse=True)  # falling outwards
    assert all(row == row[::-1] for row in alpha) and alpha == [list(r) for r in zip(*alpha)]  # round
    assert all(p[:3] == (255, 255, 255) for row in bm.rows() for p in row)


def test_minimal_greys_baidu_inverted(fw, bitmaps, scheme):
    """Its white square becomes the grey disc and the blue logo white, as its neighbours' discs and glyphs."""
    theme(fw, MINIMAL, MINIMAL_ACCENT=str(TIDAL))
    rows = fill(12, 12, WHITE)
    rows[6][6] = (0x20, 0x60, 0xff, 255)
    bm = bitmaps(fw, rows)
    image_add(fw, b'list_baidu', bm)
    grey = scheme('chevron') & 255
    assert bm.get(6, 6) == WHITE and bm.get(3, 6) == (grey, grey, grey, 255) and bm.get(0, 0)[3] == 0


def test_minimal_s_navigation_icons_are_grey_after_the_accent(fw, bitmaps):
    theme(fw, MINIMAL, MINIMAL_ACCENT=str(TIDAL))
    bm = bitmaps(fw, [[RED_PX, px(0x2040c0)]])
    image_add(fw, b'navbar_back', bm)
    tone = luma(ACCENTS[TIDAL][RED])
    assert bm.rows() == [[(tone,) * 3 + (255,), (luma(0x2040c0),) * 3 + (255,)]]


@pytest.mark.parametrize('name', [b'play_defaultcover', b'playlist_default', b'tidal_shanling', b'tidal_user'])
def test_minimal_keeps_its_placeholders_and_tidal_s_own_in_colour(fw, bitmaps, name):
    theme(fw, MINIMAL, MINIMAL_ACCENT=str(TIDAL))
    bm = bitmaps(fw, [[px(0x2040c0)]])
    image_add(fw, name, bm)
    assert bm.rows() == [[px(0x2040c0)]]


def test_minimal_s_codec_badges_take_the_muted_grey(fw, bitmaps, scheme):
    theme(fw, MINIMAL, MINIMAL_ACCENT=str(TIDAL))
    badge, delete = bitmaps(fw, [[RED_PX, CLEAR]]), bitmaps(fw, [[RED_PX, CLEAR]])
    image_add(fw, b'img_flac', badge)
    image_add(fw, b'img_delete', delete)
    assert badge.rows() == [[px(scheme('muted')), CLEAR]]
    assert delete.get(0, 0) == (luma(ACCENTS[TIDAL][RED]),) * 3 + (255,)  # an action, not a badge


@pytest.mark.parametrize('number, grey', [(MINIMAL, 1), (CLASSIC, 0)])
def test_a_card_app_s_streaming_icon_greys_under_minimal_alone(fw, bitmaps, number, grey):
    theme(fw, number, MINIMAL_ACCENT=str(TIDAL))
    bm = bitmaps(fw, [[px(0x2040c0), (0x80, 0x40, 0x20, 0x80)]])
    image_add(fw, b'file:///mnt/mmc/.q2apps/radio/icon.png', bm)
    want = [[(luma(0x2040c0),) * 3 + (255,), (luma(0x804020),) * 3 + (0x80,)]] if grey else \
           [[px(0x2040c0), (0x80, 0x40, 0x20, 0x80)]]
    assert bm.rows() == want


def test_a_card_app_s_other_files_keep_their_colours(fw, bitmaps):
    theme(fw, MINIMAL, MINIMAL_ACCENT=str(TIDAL))
    bm = bitmaps(fw, [[px(0x2040c0)]])
    image_add(fw, b'file:///mnt/mmc/.q2apps/radio/cover.png', bm)
    assert bm.rows() == [[px(0x2040c0)]]


# Classic's images.

@pytest.mark.parametrize('light', ['0', '1'])
def test_classic_s_own_images_are_final(fw, bitmaps, light):
    theme(fw, CLASSIC, LIGHT=light)
    bm = bitmaps(fw, [[RED_PX, WHITE]])
    image_add(fw, b'classic_light_band_top', bm)
    assert bm.rows() == [[RED_PX, WHITE]]


def test_classics_art_stays_final_on_a_status_bar_of_its_look(fw, bitmaps):
    theme(fw, MINIMAL, LIGHT='1', STATUSBAR='3')
    bm = bitmaps(fw, [[RED_PX, WHITE]])
    image_add(fw, b'classic_light_bolt', bm)
    assert bm.rows() == [[RED_PX, WHITE]]


@pytest.mark.parametrize('name', [b'classic_light_sort_bars', b'classic_light_vol_lo'])
def test_classic_s_marks_on_the_paper_flip_in_light_mode(fw, bitmaps, name):
    theme(fw, CLASSIC, LIGHT='1')
    bm = bitmaps(fw, [[WHITE, CLEAR]])
    image_add(fw, name, bm)
    assert bm.rows()[0][0][:3] != (255, 255, 255) and bm.get(1, 0) == CLEAR


@pytest.mark.parametrize('light', ['0', '1'])
@pytest.mark.parametrize('premul', [0, PREMULTI])
def test_classic_s_codec_badges_take_the_half_s_ink(fw, bitmaps, scheme, light, premul):
    theme(fw, CLASSIC, LIGHT=light)
    bm = bitmaps(fw, [[RED_PX, (0x40, 0x10, 0x10, 0x80), CLEAR]], flags=premul)
    image_add(fw, b'bar_ldac', bm)
    ink = channels(scheme('ink'))
    half = tuple(v * 0x80 // 255 for v in ink) if premul else ink
    zero = (0, 0, 0) if premul else ink
    assert bm.rows() == [[(*ink, 255), (*half, 0x80), (*zero, 0)]]


@pytest.mark.parametrize('name', [b'list_into', b'select', b'unselect'])
def test_classic_empties_stock_s_chevron_and_radio_rings(fw, bitmaps, name):
    """paint_into and paint_radio draw them instead: the images draw nothing, and are not opaque."""
    theme(fw, CLASSIC)
    bm = bitmaps(fw, fill(3, 2, WHITE), flags=OPAQUE | PREMULTI)
    image_add(fw, name, bm)
    assert bm.rows() == fill(3, 2, CLEAR) and bm.flags == PREMULTI and bm.padding() == {b'\x5a' * 8}


@pytest.mark.parametrize('name', [b'list_folder', b'list_song'])
def test_classic_s_grey_tiles_shrink_to_the_placeholder_size_in_their_middle(fw, bitmaps, theme_t, name):
    """The theme's art placeholder size (theme.xml's list art placeholder), square, as much room either side."""
    theme(fw, CLASSIC)
    size = theme_t('art_placeholder', C.c_short)
    bm = bitmaps(fw, fill(48, 48, px(0x606060)), flags=OPAQUE)
    image_add(fw, name, bm)
    rows = bm.rows()
    inside = [(x, y) for y in range(48) for x in range(48) if rows[y][x][3]]
    xs, ys = {x for x, _ in inside}, {y for _, y in inside}
    assert 0 < size < 48 and len(inside) == size * size and max(xs) - min(xs) == max(ys) - min(ys) == size - 1
    assert abs(min(xs) - (47 - max(xs))) <= 1 and abs(min(ys) - (47 - max(ys))) <= 1  # centred
    assert {rows[y][x] for x, y in inside} == {px(0x606060)}
    assert bm.flags & OPAQUE == 0


def test_other_themes_keep_the_grey_tiles_whole(fw, bitmaps):
    theme(fw, MODERN, ACCENT=str(TIDAL))
    bm = bitmaps(fw, fill(48, 48, px(0x606060)), flags=OPAQUE)
    image_add(fw, b'list_folder', bm)
    assert bm.rows() == fill(48, 48, px(0x606060)) and bm.flags == OPAQUE


def shrink(fw, bm, to, a=3):
    fw.fn('navigation__shrink_centred', None, C.c_void_p, C.c_int, C.c_int)(bm.header, to, a)


def test_a_shrunk_pixel_is_the_alpha_weighted_mean_of_the_block_it_covers(fw, bitmaps):
    """A 4 x 4 into 2 x 2 in its middle: each pixel from a 2 x 2 block; clear pixels add no colour."""
    rows = fill(4, 4, CLEAR)
    rows[0][0], rows[0][1] = (200, 0, 0, 255), (0, 0, 0, 0)  # block (0, 0): one red, one clear...
    rows[1][0], rows[1][1] = (100, 0, 0, 255), (0, 0, 0, 0)
    rows[0][2] = rows[0][3] = rows[1][2] = rows[1][3] = (10, 20, 30, 255)
    bm = bitmaps(fw, rows, fmt=1)
    shrink(fw, bm, 2)
    out = bm.rows()
    assert out[1][1] == (150, 0, 0, 127) and out[1][2] == (10, 20, 30, 255)
    assert out[2][1] == out[2][2] == CLEAR  # blocks of clear pixels
    assert all(out[y][x] == CLEAR for y in range(4) for x in range(4) if not (1 <= x <= 2 and 1 <= y <= 2))


def test_a_premultiplied_shrink_is_the_plain_mean(fw, bitmaps):
    rows = fill(4, 4, CLEAR)
    rows[0][0] = (200, 0, 0, 255)
    bm = bitmaps(fw, rows, flags=PREMULTI | OPAQUE)
    shrink(fw, bm, 2)
    assert bm.get(1, 1) == (50, 0, 0, 63) and bm.flags == PREMULTI


@pytest.mark.parametrize('w, h, to', [(65, 65, 38), (48, 48, 48), (48, 48, 0), (30, 48, 38)])
def test_shrink_leaves_what_it_can_t_shrink(fw, bitmaps, w, h, to):
    bm = bitmaps(fw, fill(w, h, WHITE), flags=OPAQUE)
    shrink(fw, bm, to)
    assert bitmaps.locked == [] and bm.flags == OPAQUE


# Rounded thumbnails and square discs (Corners).

SLDP = b'file:///mnt/mmc/.sldp/1f3a.jpg'


@pytest.mark.parametrize('config', [dict(number=MINIMAL), dict(number=MODERN, ROUNDED='1')])
@pytest.mark.parametrize('name', [SLDP, b'file:///mnt/mmc/.downpic/fd0007.jpg'])
def test_rounded_art_rounds_stock_s_thumbnails(fw, bitmaps, config, name):
    theme(fw, **config)
    bm = bitmaps(fw, fill(166, 166, WHITE), flags=OPAQUE)
    image_add(fw, name, bm)
    alpha = [[p[3] for p in row] for row in bm.rows()]
    r = O['NP_ART_RADIUS']  # at 166 px, the radius itself
    for y, x in [(0, 0), (0, 165), (165, 0), (165, 165)]:
        assert alpha[y][x] == 0
    assert alpha[r][r] == 255 and alpha[0][r] == 255 and alpha[r][0] == 255  # past the arc
    assert alpha[83][83] == 255 and all(row == row[::-1] for row in alpha) and alpha == alpha[::-1]
    assert any(0 < a < 255 for a in alpha[0][:r])  # an antialiased step on the arc
    assert all(alpha[y][:r] == sorted(alpha[y][:r]) for y in range(r))  # rising inwards along each row
    assert bm.flags & OPAQUE == 0 and {p[:3] for row in bm.rows() for p in row} == {(255, 255, 255)}


def test_square_art_keeps_the_thumbnails_square(fw, bitmaps):
    theme(fw, MODERN, ROUNDED='0')
    bm = bitmaps(fw, fill(20, 20, WHITE), flags=OPAQUE)
    image_add(fw, SLDP, bm)
    assert bm.rows() == fill(20, 20, WHITE) and bm.flags == OPAQUE


@pytest.mark.parametrize('name', [b'file:///mnt/mmc/Music/a.jpg', b'file:///mnt/mmc/.downpic/cover.jpg'])
def test_other_covers_are_not_rounded(fw, bitmaps, name):
    theme(fw, MINIMAL)
    bm = bitmaps(fw, fill(20, 20, WHITE), flags=OPAQUE)
    image_add(fw, name, bm)
    assert bm.rows() == fill(20, 20, WHITE)


def disc(n, edge):
    """A placeholder disc: clear corners round an opaque middle whose top edge's middle is edge."""
    rows = fill(n, n, px(0x404040))
    for y in range(n):
        for x in range(n):
            if (2 * x + 1 - n) ** 2 + (2 * y + 1 - n) ** 2 > n * n:
                rows[y][x] = (0x40, 0x40, 0x40, 0)
    rows[0][n // 2] = edge
    return rows


@pytest.mark.parametrize('name', [b'play_defaultcover', b'default_bigcover', STRINGS['PLACEHOLDER'].encode(),
                                  b'default_album_big', b'default_album_small'])
def test_square_art_fills_a_disc_placeholder_s_corners_with_its_edge(fw, bitmaps, name):
    theme(fw, MODERN, ROUNDED='0')
    bm = bitmaps(fw, disc(10, px(0x707070)))
    image_add(fw, name, bm)
    rows = bm.rows()
    assert rows[0][0] == px(0x707070) and rows[9][9] == px(0x707070)  # the corners: the edge's grey
    assert rows[5][5] == px(0x404040) and all(p[3] == 255 for row in rows for p in row)
    assert bm.flags & OPAQUE


def test_a_disc_with_a_clear_edge_stays_round(fw, bitmaps):
    theme(fw, MODERN, ROUNDED='0')
    bm = bitmaps(fw, disc(10, (0x70, 0x70, 0x70, 0)))
    before = bm.rows()
    image_add(fw, b'play_defaultcover', bm)
    assert bm.rows() == before and not bm.flags & OPAQUE


def test_rounded_art_keeps_the_discs_round(fw, bitmaps):
    theme(fw, MODERN, ROUNDED='1')
    bm = bitmaps(fw, disc(10, px(0x707070)))
    before = bm.rows()
    image_add(fw, b'play_defaultcover', bm)
    assert bm.rows() == before


# Light Mode's flip of glyph images.

@pytest.mark.parametrize('premul', [0, PREMULTI])
def test_light_mode_flips_a_glyph_image(fw, bitmaps, premul):
    """White marks on clear turn the light half's ink, a half-covered pixel its own share of it."""
    theme(fw, MODERN, ACCENT=str(TIDAL), LIGHT='1')
    edge = (0x80, 0x80, 0x80, 0x80) if premul else (255, 255, 255, 0x80)
    bm = bitmaps(fw, [[WHITE, edge, CLEAR]], flags=premul)
    image_add(fw, b'list_more', bm)
    flipped = (INK * 0x80 // 255,) * 3 if premul else (INK,) * 3
    assert bm.rows() == [[(INK, INK, INK, 255), (*flipped, 0x80), CLEAR]]


def test_light_mode_keeps_a_dark_disc_s_white_glyph(fw, bitmaps):
    """Mostly dark (a disc or plate): the white glyph on it stays white."""
    theme(fw, MODERN, ACCENT=str(TIDAL), LIGHT='1')
    bm = bitmaps(fw, [[px(0x303030)] * 3 + [WHITE]])
    image_add(fw, b'list_disc', bm)
    assert bm.rows() == [[px(0x303030)] * 3 + [WHITE]]


def test_light_mode_keeps_a_coloured_image(fw, bitmaps):
    theme(fw, MODERN, ACCENT=str(TIDAL), LIGHT='1')
    bm = bitmaps(fw, [[px(0x2080ff), WHITE]])
    image_add(fw, b'list_more', bm)
    assert bm.rows() == [[px(0x2080ff), WHITE]]


def test_light_mode_flips_a_quick_control_s_inactive_disc_whole(fw, bitmaps):
    theme(fw, MODERN, ACCENT=str(TIDAL), LIGHT='1')
    rows = [[px(0x303030)] * 3 + [WHITE]]
    control, plate = bitmaps(fw, rows), bitmaps(fw, rows)
    image_add(fw, STRINGS['DROPDOWN_IMAGE'].encode() + b'bt_off', control)
    image_add(fw, b'list_disc', plate)
    assert control.get(3, 0) == (INK, INK, INK, 255) and control.get(0, 0)[0] > 0x30
    assert plate.rows() == rows


def test_light_mode_keeps_switch_off_s_white_knob(fw, bitmaps):
    theme(fw, MODERN, ACCENT=str(TIDAL), LIGHT='1')
    bm = bitmaps(fw, [[WHITE, CLEAR]])
    image_add(fw, b'switch_off', bm)
    assert bm.rows() == [[WHITE, CLEAR]]


@pytest.mark.parametrize('name', [b'scrsaver_slide', b'gain_low', b'gain_high'])
@pytest.mark.parametrize('number, flips', [(CLASSIC, 0), (MODERN, 1)])
def test_a_theme_s_own_light_half_keeps_the_lock_hint_and_gain_s_thumb_white(fw, bitmaps, name, number, flips):
    theme(fw, number, ACCENT=str(TIDAL), LIGHT='1')
    bm = bitmaps(fw, [[WHITE, CLEAR]])
    image_add(fw, name, bm)
    assert bm.get(0, 0) == ((INK, INK, INK, 255) if flips else WHITE)


def test_light_mode_leaves_an_image_the_accent_mapped(fw, bitmaps):
    theme(fw, MODERN, ACCENT=str(TIDAL), LIGHT='1')
    bm = bitmaps(fw, [[RED_PX, WHITE, WHITE, WHITE]])
    image_add(fw, b'list_more', bm)
    assert bm.rows()[0][1:] == [WHITE] * 3


# The colour hooks.

def style_color(fw, stock, name, fallback=0, inner=None):
    """The colour hook over stock's answer, a color_t: what the widget gets. inner, if set, is a property stock
    asks the hook for again from inside its own answer (a mutable style through its default one): then the
    outer answer and the inner one."""
    hook = fw.fn('ringnav_style_color', C.c_void_p, C.c_void_p, C.c_void_p, C.c_char_p, C.c_uint)
    nested = []

    def stock_color(out, style, n, fb, *_):
        C.c_uint.from_address(out).value = stock
        if inner and not nested:
            nested.append(1)
            again = C.c_uint(0)
            hook(C.addressof(again), style, inner, fb)
            nested.append(again.value)
    fw.mock('stock_color_trampoline', stock_color)
    out = C.c_uint(0)
    assert hook(C.addressof(out), 0x7000, name, fallback) == C.addressof(out)
    return (out.value, nested[1]) if inner else out.value


@pytest.mark.parametrize('name, tone', [(b'bg_color', LIGHT), (b'border_color', LIGHT), (b'fg_color', LIGHT),
                                        (b'text_color', RED), (b'highlight_text_color', RED)])
@pytest.mark.parametrize('alpha', [0xff, 0x40])
def test_stock_red_takes_the_light_tone_and_text_the_red_one(fw, name, tone, alpha):
    theme(fw, MODERN, ACCENT=str(GRAPHITE))
    assert style_color(fw, rgba(STOCK_RED, alpha), name) == rgba(ACCENTS[GRAPHITE][tone], alpha)


@pytest.mark.parametrize('color', [rgba(0x808080), rgba(0x2050c0), rgba(0xffffff, 0x80)])
def test_other_colours_stay(fw, color):
    theme(fw, MODERN, ACCENT=str(GRAPHITE))
    assert style_color(fw, color, b'bg_color') == style_color(fw, color, b'text_color') == color


def test_an_edit_s_selected_text_keeps_stock_s_colour(fw):
    theme(fw, MODERN, ACCENT=str(GRAPHITE), LIGHT='1')
    assert style_color(fw, rgba(STOCK_RED), b'selected_text_color') == rgba(STOCK_RED)
    assert style_color(fw, rgba(0x101010), b'selected_text_color') == rgba(0x101010)  # not flipped to paper


def test_crimson_s_dark_half_is_stock(fw):
    theme(fw, MODERN, ACCENT=str(CRIMSON))
    assert style_color(fw, rgba(STOCK_RED), b'bg_color') == rgba(STOCK_RED)
    assert style_color(fw, rgba(STOCK_RED), b'text_color') == rgba(STOCK_RED)


@pytest.mark.parametrize('name, stock, light', [(b'text_color', 0xffffff, INK), (b'bg_color', 0x000000, PAPER)])
def test_only_the_outermost_ask_maps(fw, name, stock, light):
    """Light Mode's flip is not its own inverse: a style that asks again from inside its answer is mapped once."""
    theme(fw, MODERN, ACCENT=str(TIDAL), LIGHT='1')
    once = style_color(fw, rgba(stock), name)
    outer, inner = style_color(fw, rgba(stock), name, inner=name)
    assert once == outer == rgba(light * 0x010101) and inner == rgba(stock)


@pytest.mark.parametrize('number, same, stock_hint', [(CLASSIC, 1, 1), (CLASSIC, 0, 0), (MODERN, 1, 0)])
def test_classic_s_hint_with_no_colour_of_its_own_is_already_mapped(fw, number, same, stock_hint):
    """Its fallback is the text colour Light Mode already flipped: flipping it again would hide it."""
    theme(fw, number, LIGHT='1')
    white = rgba(0xffffff)
    got = style_color(fw, white, b'tips_text_color', fallback=white if same else rgba(0x808080))
    assert (got == white) == bool(stock_hint)


# The style hooks read where the paint is: widgets as stock's calls see them.

class Node:
    def __init__(self, name, kind, parent):
        self.block = C.create_string_buffer(0x400)
        self.p = C.addressof(self.block)
        self.name, self.kind, self.parent = name, kind, parent
        self.props, self.children, self.text, self.visible = {}, [], None, 1


def wide(s, n=None):
    """A wide string from the C, n characters or to its NUL."""
    if not s:
        return None
    out = []
    while (n is None or len(out) < n) and (c := C.c_uint.from_address(s + 4 * len(out)).value):
        out.append(chr(c))
    return ''.join(out)


def rect(r):
    return tuple((C.c_int * 4).from_address(r)) if r else None


class World:
    """Stock's widgets: each a zeroed block holding its box and its parent, with a name, a type, properties, a
    text, its visibility and children, which stock's calls read and write as the C makes them. Timers, handlers
    and invalidations are kept."""
    def __init__(self, fw):
        self.fw, self.nodes, self.strs, self.wides = fw, {}, {}, []
        self.handlers, self.timers, self.invalid, self.removed = [], [], [], []
        self.wm = self.add(None, 'window_manager', 'window_manager', box=(0, 0, 375, 320))
        self.top = 0
        m = fw.mock
        m('window_manager', lambda *_: self.wm)
        m('window_manager_get_top_window', lambda *_: self.top)
        m('widget_lookup', self.lookup)
        m('widget_get_prop_str', self.get_str)
        m('widget_get_prop_int', self.get_int)
        m('widget_get_prop_bool', self.get_int)
        m('widget_set_prop_int', lambda w, k, v, *_: self.set(w, k, C.c_int(v).value))
        m('widget_set_prop_str', lambda w, k, v, *_: self.set(w, k, C.string_at(v).decode()))
        m('widget_get_type', lambda w, *_: self.cstr(self.nodes[w].kind) if w in self.nodes else 0)
        m('widget_count_children', lambda w, *_: len(self.nodes[w].children) if w in self.nodes else 0)
        m('widget_get_child', self.child)
        m('widget_get_text', self.get_text)
        m('widget_get_text_utf8', self.get_utf8)
        m('widget_set_text_utf8', lambda w, s, *_: self.set_text(w, C.string_at(s).decode()))
        m('widget_set_text', lambda w, s, *_: self.set_text(w, wide(s)))
        m('widget_get_visible', lambda w, *_: self.nodes[w].visible if w in self.nodes else 0)
        m('widget_set_visible', lambda w, v, *_: self.node(w).__setattr__('visible', v))
        m('widget_move_resize', lambda w, x, y, ww, h, *_: self.box(w, x, y, ww, h))
        m('widget_resize', lambda w, ww, h, *_: self.box(w, self.at(w)[0], self.at(w)[1], ww, h))
        m('widget_set_name', lambda w, s, *_: self.node(w).__setattr__('name', C.string_at(s).decode()))
        m('widget_use_style', lambda w, s, *_: self.set(w, 'style', C.string_at(s).decode()))
        for kind in ('hscroll_label', 'label', 'image', 'button'):
            m(f'{kind}_create', lambda parent, x, y, ww, h, *_, kind=kind: self.add(parent, None, kind, (x, y, ww, h)))
        m('widget_on', lambda w, evt, fn, ctx, *_: self.handlers.append((w, evt, fn, ctx)) or len(self.handlers))
        m('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, ctx, ms)) or len(self.timers))
        m('widget_add_timer', lambda w, fn, ms, *_: self.timers.append((fn, w, ms)) or len(self.timers))
        m('timer_remove', lambda t, *_: self.removed.append(t))
        m('widget_invalidate_force', lambda w, r, *_: self.invalid.append((w, rect(r))))

    def add(self, parent, name, kind='view', box=(0, 0, 375, 320), text=None, **props):
        node = Node(name, kind, parent)
        node.props.update(props)
        node.text = text
        self.nodes[node.p] = node
        struct.pack_into('<iiii', node.block, W_X, *box)
        if parent:
            struct.pack_into('<Q', node.block, W_PARENT, parent)
            self.nodes[parent].children.append(node.p)
        return node.p

    def window(self, name, kind='window', box=(0, 0, 375, 320), top=True, **props):
        win = self.add(self.wm, name, kind, box, **props)
        if top:
            self.top = win
        return win

    def node(self, w):
        return self.nodes.get(w) or Node(None, None, 0)

    def __getitem__(self, w):
        return self.nodes[w]

    def child(self, w, i, *_):
        kids = self.nodes[w].children if w in self.nodes else []
        return kids[i] if 0 <= i < len(kids) else 0

    def at(self, w):
        return struct.unpack_from('<iiii', self.nodes[w].block, W_X)

    def box(self, w, x, y, ww, h):
        if w in self.nodes:
            struct.pack_into('<iiii', self.nodes[w].block, W_X, *(C.c_int(v).value for v in (x, y, ww, h)))

    def cstr(self, s):
        if s not in self.strs:
            self.strs[s] = C.create_string_buffer(s.encode())
        return C.addressof(self.strs[s])

    def find(self, parent, name, deep=True):
        for child in self.nodes[parent].children:
            if self.nodes[child].name == name:
                return child
            if deep and (found := self.find(child, name)):
                return found
        return 0

    def lookup(self, parent, name, deep, *_):
        return self.find(parent, C.string_at(name).decode(), bool(deep)) if parent in self.nodes else 0

    def get_str(self, w, key, default, *_):
        key, node = C.string_at(key).decode(), self.nodes.get(w)
        value = None if node is None else node.name if key == 'name' else node.props.get(key)
        return self.cstr(value) if isinstance(value, str) else default

    def get_int(self, w, key, default, *_):
        value = self.nodes[w].props.get(C.string_at(key).decode()) if w in self.nodes else None
        return value if isinstance(value, int) else C.c_int(default).value

    def set(self, w, key, value):
        self.node(w).props[C.string_at(key).decode() if isinstance(key, int) else key] = value

    def get_text(self, w, *_):
        text = self.nodes[w].text if w in self.nodes else None
        if text is None:
            return 0
        self.wides.append((C.c_uint * (len(text) + 1))(*map(ord, text), 0))
        return C.addressof(self.wides[-1])

    def get_utf8(self, w, out, size, *_):
        if w not in self.nodes:
            return 1  # RET_BAD_PARAMS
        data = (self.nodes[w].text or '').encode()[:size - 1]
        C.memmove(out, data + b'\0', len(data) + 1)
        return 0

    def set_text(self, w, s):
        self.node(w).text = s

    def on(self, w, event):
        """w's handlers for event, each run with its context as stock would."""
        return [lambda f=fn, c=ctx: C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(f)(c, 0)
                for target, evt, fn, ctx in self.handlers if target == w and evt == event]

    def fire(self, timer):
        """Run timer (timer_add's number) as stock would: its answer."""
        fn, ctx, _ = self.timers[timer - 1]
        info = C.create_string_buffer(0x40)
        struct.pack_into('<Q', info, O['TIMER_CTX'], ctx or 0)
        return C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(C.addressof(info))


@pytest.fixture
def world(fw):
    return World(fw)


def test_classic_s_scrollbar_draws_its_own_thumb(fw, st, world):
    theme(fw, CLASSIC)
    bar = world.add(world.window('list_page'), 'bar', 'scroll_bar_m')
    st('paint_w', C.c_void_p).value = bar
    assert style_color(fw, rgba(0x808080), b'fg_color') == rgba(0x808080, 0)
    assert style_color(fw, rgba(0x404040), b'bg_color') == rgba(0x404040, 0)
    assert style_color(fw, rgba(0x404040), b'border_color') == rgba(0x404040)


def test_a_theme_without_its_own_thumb_keeps_stock_s(fw, st, world):
    theme(fw, MODERN, ACCENT=str(TIDAL))
    st('paint_w', C.c_void_p).value = world.add(world.window('list_page'), 'bar', 'scroll_bar_m')
    assert style_color(fw, rgba(0x808080), b'fg_color') == rgba(0x808080)


def test_the_keyboard_s_keys_draw_their_own_text(fw, st, world):
    theme(fw, CLASSIC)
    kb = world.window('kb_default_t9')
    key = world.add(world.add(world.add(world.add(kb, 'panel'), 'page'), 'row'), 'a', 'button')
    st('kb_win', C.c_void_p).value = kb
    st('paint_w', C.c_void_p).value = key
    assert style_color(fw, rgba(0xffffff), b'text_color') == rgba(0xffffff, 0)
    st('paint_w', C.c_void_p).value = world.find(kb, 'row')  # not a key: three up, not four
    assert style_color(fw, rgba(0xffffff), b'text_color') == rgba(0xffffff)


def test_minimal_s_caps_draw_the_bar_s_idle_text(fw, st, world):
    theme(fw, MINIMAL, MINIMAL_ACCENT=str(TIDAL))
    idle = world.add(world.window('system_bar'), 'label_idle', 'label')
    st('bar_idle', C.c_void_p).value = st('paint_w', C.c_void_p).value = idle
    assert style_color(fw, rgba(0xffffff), b'text_color') == rgba(0xffffff, 0)


@pytest.mark.parametrize('enabled, grey', [((1, 1), 0xffffff), ((1, 0), 0xaaaaaa), ((0, 1), 0xaaaaaa)])
def test_a_playlist_row_s_delete_and_rename_grey_under_a_disabled_parent(fw, st, world, enabled, grey):
    theme(fw, MODERN, ACCENT=str(TIDAL))
    win = world.window('playlist_page', enable=enabled[0])
    label = world.add(world.add(win, 'view_bottom', enable=enabled[1]), 'label_del', 'label')
    st('paint_w', C.c_void_p).value = label
    assert style_color(fw, rgba(0x123456, 0xc0), b'text_color') == rgba(grey, 0xc0)


def selection(world, st):
    """A list's selected row and a label on it, being painted: (surface, row, label)."""
    surface = world.add(world.window('list_page'), 'list', 'scroll_view')
    row = world.add(surface, 'row', 'list_item')
    label = world.add(row, 'title', 'label')
    st('sel_w', C.c_void_p).value = surface
    st('paint_w', C.c_void_p).value = label
    return surface, row, label


def test_classic_s_selection_text_is_its_own(fw, st, world, scheme):
    theme(fw, CLASSIC)
    _, row, _ = selection(world, st)
    st('sel_accent', C.c_void_p).value = row
    assert style_color(fw, rgba(0x808080, 0x80), b'text_color') == rgba(scheme('sel_text'), 0x80)
    st('sel_accent', C.c_void_p).value = 0
    assert style_color(fw, rgba(0x808080, 0x80), b'text_color') == rgba(0x808080, 0x80)


def test_ivory_s_card_darkens_the_selected_row_s_text(fw, st, world, scheme):
    """On the off-white card (the scheme's fill) white text would vanish: both inks darker than the card, the
    title's darker than a second line's, which every grey shares; alpha kept."""
    theme(fw, MINIMAL, MINIMAL_ACCENT=str(IVORY))
    _, row, _ = selection(world, st)
    st('sel_widget', C.c_void_p).value = row
    title, grey, quieter = (style_color(fw, rgba(c, 0xc0), b'text_color') for c in (0xffffff, 0xaaaaaa, 0x8c8c8c))
    assert {c >> 24 for c in (title, grey, quieter)} == {0xc0}
    assert luma(rgb_of(title)) < luma(rgb_of(grey)) < luma(scheme('fill')) and grey == quieter


def test_home_s_selected_row_is_white_and_the_rest_grey(fw, st, world):
    theme(fw, MINIMAL, MINIMAL_ACCENT=str(TIDAL))
    surface, row, label = selection(world, st)
    st('sel_home').value = 1
    st('sel_widget', C.c_void_p).value = row
    assert style_color(fw, rgba(0x808080), b'text_color') == rgba(0xffffff)
    other = world.add(world.add(surface, 'row2', 'list_item'), 'title', 'label')
    st('paint_w', C.c_void_p).value = other
    assert style_color(fw, rgba(0xffffff), b'text_color') == rgba(0xaaaaaa)


def test_minimal_s_secondary_text_is_its_quieter_grey(fw, st, world, scheme):
    theme(fw, MINIMAL, MINIMAL_ACCENT=str(TIDAL))
    _, row, _ = selection(world, st)
    assert style_color(fw, rgba(0xaaaaaa), b'text_color') == rgba(scheme('muted'))
    st('sel_widget', C.c_void_p).value = row  # on the selected row: stock's grey
    assert style_color(fw, rgba(0xaaaaaa), b'text_color') == rgba(0xaaaaaa)


def gradient_hook(fw, colors, name=b'bg_color', own=False):
    """A style whose vtable's get_gradient fills the caller's gradient_t with colors (its own cached one when
    own): the stops after the hook, the gradient it answered, and what must outlive the call. A gradient_t is
    0x4c bytes, 8 stops; the room past it is someone else's."""
    out = C.create_string_buffer(0x60)
    cached = C.create_string_buffer(0x60)
    target = cached if own else out
    getter = C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_char_p, C.c_void_p)(
        lambda style, n, o: struct.pack_into(f'<I{2 * len(colors)}I', target, 8, len(colors),
                                             *(v for i, c in enumerate(colors) for v in (c, i))) or C.addressof(target))
    vtable = (C.c_void_p * 4)(0, 0, 0, C.cast(getter, C.c_void_p).value)
    style = C.c_void_p(C.addressof(vtable))
    got = fw.fn('ringnav_style_gradient', C.c_void_p, C.c_void_p, C.c_char_p, C.c_void_p)(
        C.addressof(style), name, C.addressof(out))
    stops = struct.unpack_from(f'<{2 * len(colors)}I', target, 12)
    return list(stops[::2]), got, (C.addressof(target), getter, vtable)


def test_a_gradient_s_stops_take_the_light_tone(fw):
    theme(fw, MODERN, ACCENT=str(GRAPHITE))
    stops, got, keep = gradient_hook(fw, [rgba(STOCK_RED), rgba(0x808080), rgba(STOCK_RED, 0x40)])
    assert stops == [rgba(ACCENTS[GRAPHITE][LIGHT]), rgba(0x808080), rgba(ACCENTS[GRAPHITE][LIGHT], 0x40)]
    assert got == keep[0]


def test_a_gradient_maps_its_eight_stops_and_nothing_past_them(fw):
    theme(fw, MODERN, ACCENT=str(GRAPHITE))
    stops, _, _ = gradient_hook(fw, [rgba(STOCK_RED)] * 10)  # a count past what gradient_t holds
    assert stops == [rgba(ACCENTS[GRAPHITE][LIGHT])] * 8 + [rgba(STOCK_RED)] * 2


def test_a_style_s_own_cached_gradient_is_left(fw):
    theme(fw, MODERN, ACCENT=str(GRAPHITE))
    stops, got, keep = gradient_hook(fw, [rgba(STOCK_RED)], own=True)
    assert stops == [rgba(STOCK_RED)] and got == keep[0]


def test_no_style_has_no_gradient(fw):
    theme(fw, MODERN, ACCENT=str(GRAPHITE))
    hook = fw.fn('ringnav_style_gradient', C.c_void_p, C.c_void_p, C.c_char_p, C.c_void_p)
    out = C.create_string_buffer(0x4c)
    assert hook(None, b'bg_color', C.addressof(out)) is None
    vtable = (C.c_void_p * 4)()
    style = C.c_void_p(C.addressof(vtable))  # a vtable with no get_gradient
    assert hook(C.addressof(style), b'bg_color', C.addressof(out)) is None


def test_the_selected_row_s_background_is_clear(fw, st, world):
    """The selection painter draws it: stock's background over it would hide the bar."""
    theme(fw, MODERN, ACCENT=str(GRAPHITE))
    _, row, _ = selection(world, st)
    st('sel_widget', C.c_void_p).value = row
    assert gradient_hook(fw, [rgba(STOCK_RED), rgba(0x808080)])[0] == [0, 0]
    assert gradient_hook(fw, [rgba(STOCK_RED)], name=b'border_color')[0] == [rgba(ACCENTS[GRAPHITE][LIGHT])]


def test_a_gradient_asked_for_a_colour_is_left_to_the_colour_hook(fw):
    """style_get_color asks for the gradient first: the colour hook alone picks the tone, text's or not."""
    theme(fw, MODERN, ACCENT=str(GRAPHITE))
    seen = []

    def stock_color(out, style, name, fb, *_):
        seen.append(gradient_hook(fw, [rgba(STOCK_RED)])[0])
        C.c_uint.from_address(out).value = rgba(STOCK_RED)
    fw.mock('stock_color_trampoline', stock_color)
    out = C.c_uint(0)
    fw.fn('ringnav_style_color', C.c_void_p, C.c_void_p, C.c_void_p, C.c_char_p, C.c_uint)(
        C.addressof(out), 0x7000, b'text_color', 0)
    assert seen == [[rgba(STOCK_RED)]] and out.value == rgba(ACCENTS[GRAPHITE][RED])


# The background hook: what it paints, under what it leaves to stock.

class Canvas:
    """A canvas and its lcd, whose fill colour stock's setter writes: the fills (x, y, w, h, color), the rounded
    boxes (rect, color, radius), the theme images drawn (name, dst rect; every image 20 x 10) and the text
    (text, rect, colour, (face, px))."""
    def __init__(self, fw, fill=0xff123456, lcd=True):
        self.lcd = C.create_string_buffer(0x200)
        self.raw = C.create_string_buffer(0x100)
        if lcd:
            struct.pack_into('<Q', self.raw, CANVAS_LCD, C.addressof(self.lcd))
        struct.pack_into('<I', self.lcd, LCD_FILL_COLOR, fill)
        self.p = C.addressof(self.raw)
        self.fills, self.rounded, self.arts, self.texts, self.loaded = [], [], [], [], {}
        self.text_color, self.font = 0, (None, 0)
        fw.mock('canvas_set_fill_color', lambda c, color, *_: struct.pack_into('<I', self.lcd, LCD_FILL_COLOR, color & 0xffffffff))
        fw.mock('canvas_fill_rect', lambda c, x, y, w, h, *_: self.fills.append(
            (*(C.c_int(v).value for v in (x, y, w, h)), self.fill)))
        fw.mock('canvas_fill_rounded_rect', lambda c, r, bg, color, radius, *_: self.rounded.append(
            (rect(r), C.c_uint.from_address(color).value, radius)))
        fw.mock('canvas_set_text_color', lambda c, color, *_: setattr(self, 'text_color', color & 0xffffffff))
        fw.mock('canvas_set_font', lambda c, face, px, *_: setattr(self, 'font', (C.string_at(face).decode() if face else None, px)))
        fw.mock('canvas_draw_text_in_rect', lambda c, s, n, r, *_: self.texts.append((wide(s, n), rect(r), self.text_color, self.font)))
        fw.mock('canvas_measure_text', lambda c, s, n, *_: 10.0 * n)  # 10 px a character
        fw.mock('widget_load_image', self.load)
        fw.mock('canvas_draw_image', lambda c, bm, src, dst, *_: self.arts.append((self.loaded.get(bm), rect(dst))))
        self.clip = (0, 0, 375, 480)  # the whole screen
        fw.mock('canvas_get_clip_rect', lambda c, r, *_: C.memmove(r, struct.pack('<4i', *self.clip), 16) and 0)

    @property
    def fill(self):
        return struct.unpack_from('<I', self.lcd, LCD_FILL_COLOR)[0]

    def load(self, w, name, bitmap, *_):
        self.loaded[bitmap] = C.string_at(name).decode()
        C.memmove(bitmap, struct.pack('<ii', 20, 10), 8)
        return 0

    def art(self, name):
        return [dst for n, dst in self.arts if n == name]

    def rows(self, x=None, w=None):
        """The one-pixel-high fills, by row: {y: color}, those at x, w wide if given."""
        return {y: c for fx, y, fw_, h, c in self.fills if h == 1 and (x is None or fx == x) and (w is None or fw_ == w)}


def paint_bg(fw, w, canvas, stock=0):
    """The hook over stock's own background paint, which answers stock: the hook's answer."""
    fw.mock('stock_paint_bg_trampoline', lambda *_: stock)
    return fw.fn('ringnav_paint_bg', C.c_int, C.c_void_p, C.c_void_p)(w, canvas.p)


PH_GREY = O['PH_GREY']


def press_row(world, width=375, h=52, style='s_btn_listitem', state='pressed', tile=False):
    """A list row and the button over its top, pressed: (row, button)."""
    surface = world.add(world.window('list_page'), 'list', 'scroll_view')
    row = world.add(surface, 'row', 'list_item', box=(0, 0, width, h))
    button = world.add(row, 'btn', 'button', box=(0, 0, width // 3 if tile else width, h), style=style,
                       state_for_style=state)
    return row, button


@pytest.mark.parametrize('rounded, inset', [('0', 0), ('1', O['SEL_INSET'])])
def test_a_pressed_row_is_grey_in_the_selection_s_shape(fw, world, rounded, inset):
    """The whole row square, or under Rounded Corners the selection's pill, SEL_INSET in, its ends half its
    height round; the button's own box is skipped."""
    theme(fw, MINIMAL, ROUNDED=rounded)
    row, button = press_row(world)
    canvas = Canvas(fw)
    assert paint_bg(fw, row, canvas, stock=5) == 5  # the row's own (clear) background is still stock's
    _, _, w, h = world.at(row)
    assert canvas.rounded == [((inset, 0, w - 2 * inset, h), PH_GREY, h // 2 if inset else 0)]
    assert paint_bg(fw, button, canvas, stock=5) == 0 and len(fw.calls('stock_paint_bg_trampoline')) == 1


@pytest.mark.parametrize('style', ['s_btn_forwardspeed', 's_listitem_black'])
def test_the_other_list_buttons_press_grey_too(fw, world, style):
    theme(fw, MINIMAL)
    row, _ = press_row(world, style=style)
    canvas = Canvas(fw)
    paint_bg(fw, row, canvas)
    assert [c for _, c, _ in canvas.rounded] == [PH_GREY]


@pytest.mark.parametrize('style, state', [('s_btn_white', 'pressed'), ('s_btn_listitem', 'normal')])
def test_an_unpressed_or_other_button_is_stock_s(fw, world, style, state):
    theme(fw, MINIMAL)
    row, button = press_row(world, style=style, state=state)
    canvas = Canvas(fw)
    paint_bg(fw, row, canvas)
    paint_bg(fw, button, canvas)
    assert canvas.rounded == [] and len(fw.calls('stock_paint_bg_trampoline')) == 2


def test_a_released_row_repaints_its_edges_once_after(fw, world):
    """The press invalidated the whole surface, the release only the button: the row's edges wait for a
    repaint of the top window, once."""
    theme(fw, MINIMAL)
    row, button = press_row(world)
    canvas = Canvas(fw)
    paint_bg(fw, row, canvas)
    world.set(button, 'state_for_style', 'normal')
    paint_bg(fw, row, canvas)
    (fn, _, ms), = world.timers
    assert ms == 0 and world.fire(1) == RET_OK and world.invalid == [(world.top, None)]
    paint_bg(fw, row, canvas)
    assert len(world.timers) == 1  # once


@pytest.mark.parametrize('rounded, radius', [('0', 0), ('1', O['SEL_RADIUS'])])
def test_a_pressed_tile_is_grey_inside_its_card(fw, world, rounded, radius):
    theme(fw, MINIMAL, ROUNDED=rounded)
    row, tile = press_row(world, tile=True)
    canvas = Canvas(fw)
    paint_bg(fw, row, canvas)
    assert canvas.rounded == []  # the tile draws its own
    assert paint_bg(fw, tile, canvas, stock=5) == 0
    inset = O['TILE_INSET']
    assert canvas.rounded == [((inset, inset, 125 - 2 * inset, 52 - 2 * inset), PH_GREY, radius)]


def test_a_row_its_own_target_draws_its_pill_as_its_background(fw, world):
    """A page row (its own touch target, no button over it): its clear background is the pill's."""
    theme(fw, MINIMAL, ROUNDED='0')
    surface = world.add(world.window('list_page'), 'list', 'scroll_view')
    row = world.add(surface, 'row', 'list_item', box=(0, 0, 375, 52), style='s_listitem_black', state_for_style='pressed')
    world.add(row, 'title', 'label', box=(20, 0, 300, 52))
    canvas = Canvas(fw)
    assert paint_bg(fw, row, canvas, stock=5) == 0 and canvas.rounded == [((0, 0, 375, 52), PH_GREY, 0)]


def test_a_childless_page_is_no_row(fw, world):
    theme(fw, MINIMAL)
    surface = world.add(world.window('playing_page'), 'list', 'scroll_view')
    page = world.add(surface, 'vis', 'list_item', box=(0, 0, 375, 52), style='s_listitem_black', state_for_style='pressed')
    canvas = Canvas(fw)
    paint_bg(fw, page, canvas)
    assert canvas.rounded == []


# Album grids' tiles.

def album_grid(world, window='album_page', w=110, h=160, kind='button'):
    tile = world.add(world.add(world.window(window), 'grid', 'scroll_view'), 'tile', kind, box=(0, 0, w, h))
    cover = world.add(tile, 'cover', 'image', box=(0, 0, 110, 110))
    overlay = world.add(cover, 'playing', 'image', box=(0, 0, 110, 110))
    badge = world.add(tile, 'badge', 'image', box=(86, 4, 20, 20))
    name = world.add(tile, 'name', 'hscroll_label', box=(0, 115, 110, 20))
    return tile, cover, overlay, badge, name


@pytest.mark.parametrize('window', ['album_page', 'artistinfo_page'])
def test_an_album_tile_insets_its_cover_and_name_inside_the_card(fw, world, window):
    theme(fw, MINIMAL)
    fw.mock('image_set_draw_type', lambda w, t, *_: world.set(w, 'draw_type', t))
    tile, cover, overlay, badge, name = album_grid(world, window)
    paint_bg(fw, tile, Canvas(fw))
    pad = O['TILE_INSET'] + O['TILE_CLEARANCE']
    assert world.at(cover) == (pad, pad, 110 - 2 * pad, 110 - 2 * pad)  # the same margin on all four sides
    assert world.at(overlay) == (0, 0, 110 - 2 * pad, 110 - 2 * pad)  # nested: at the cover's own origin
    assert world.at(name) == (pad, 115 - O['TILE_INSET'], 110 - 2 * pad, 20)  # under the cover, as wide
    assert world.at(badge) == (86, 4, 20, 20)
    assert world[cover].props['draw_type'] == world[overlay].props['draw_type'] == O['IMAGE_DRAW_SCALE_DOWN']
    moves = len(fw.calls('widget_move_resize'))
    paint_bg(fw, tile, Canvas(fw))
    assert len(fw.calls('widget_move_resize')) == moves and world.at(name)[1] == 115 - O['TILE_INSET']  # once


@pytest.mark.parametrize('window, w, h, kind', [('home_page', 110, 160, 'button'), ('album_page', 110, 150, 'button'),
                                                 ('album_page', 100, 160, 'button'), ('album_page', 110, 160, 'view')])
def test_anything_else_keeps_its_layout(fw, world, window, w, h, kind):
    theme(fw, MINIMAL)
    tile, cover, *_ = album_grid(world, window, w, h, kind)
    paint_bg(fw, tile, Canvas(fw))
    assert world.at(cover) == (0, 0, 110, 110) and fw.calls('widget_move_resize') == []


# The confirm pop-up's prompt, outside Classic.

def confirm(world, check=None):
    win = world.window('confirminfo_dialog', 'dialog')
    shade = world.add(win, 'shade', 'view', box=(0, 0, 375, 320))
    prompt = world.add(win, 'prompt', 'label', box=(30, 60, 315, 40))
    note = world.add(win, 'note', 'label', box=(30, 200, 315, 20))  # it ends past the rows' top
    ok = world.add(win, 'btn_ok', 'button', box=(30, 220, 150, 60))
    box = world.add(win, 'msg_delcheckbutton', 'check_button', box=(30, check, 315, 20)) if check else 0
    return win, shade, prompt, note, ok, box


@pytest.mark.parametrize('check, bottom', [(None, O['CONFIRM_TOP']), (124, 124)])
def test_the_prompt_fills_the_space_above_the_rows_centred(fw, world, check, bottom):
    theme(fw, MINIMAL)
    win, shade, prompt, note, ok, box = confirm(world, check)
    paint_bg(fw, win, Canvas(fw))
    assert world.at(prompt) == (0, 0, 375, bottom)
    assert world[prompt].props == {'style:normal:font_size': O['CONFIRM_PROMPT_PX'], 'style:normal:text_align_h': 'center',
                                   'style:normal:text_align_v': 'middle'}
    for kept, place in [(shade, (0, 0, 375, 320)), (note, (30, 200, 315, 20)), (ok, (30, 220, 150, 60))]:
        assert world.at(kept) == place and world[kept].props.get('style:normal:font_size') is None
    if box:
        assert world.at(box) == (30, 124, 315, 20)


def test_another_dialog_s_labels_stay(fw, world):
    theme(fw, MINIMAL)
    win = world.window('msginfo_dialog', 'dialog')
    prompt = world.add(win, 'prompt', 'label', box=(30, 60, 315, 40))
    paint_bg(fw, win, Canvas(fw))
    assert world.at(prompt) == (30, 60, 315, 40)


# Quick Settings.

def test_named_in_a_nul_separated_list(fw):
    named = fw.fn('navigation__named_in', C.c_int, C.c_char_p, C.c_char_p)
    names = b'drop_wifi\0drop_bt\0drop_lo\0'
    assert [named(names, n) for n in (b'drop_wifi', b'drop_bt', b'drop_lo', b'drop', b'drop_btx', b'')] == [1, 1, 1, 0, 0, 0]
    assert named(b'', b'drop_wifi') == 0


SILVER = O['GLYPH_LIGHT_MAX']


@pytest.mark.parametrize('tone, bright', [(0xffffff, 1), (0x000000, 0), (SILVER * 0x010101, 0), ((SILVER + 1) * 0x010101, 1),
                                          *((a[RED], int(luma(a[RED]) > SILVER)) for a in ACCENTS)])
def test_a_bright_tone_is_one_over_graphite_s_silver_line(fw, tone, bright):
    """Perceived brightness over GLYPH_LIGHT_MAX: a grey's is its own level, so the line falls between two greys."""
    assert fw.fn('navigation__bright_tone', C.c_int, C.c_uint)(tone) == bright


def quick_window(world, **icons):
    win = world.window('statusbar_dialog', 'dialog')
    view = world.add(win, 'view_backlight', box=(0, 250, 375, 60))
    slider = world.add(view, 'slider_backlight', 'slider', box=(40, 10, 300, 40), min=0, max=100, value=50)
    return win, view, slider


def fill_props(world, slider):
    return {k: v for k, v in world[slider].props.items() if k.endswith(('fg_color', 'bg_color')) or k == '_ringnav_fill'}


def test_quick_settings_brightness_fills_in_the_accent_once(fw, world):
    theme(fw, MINIMAL, MINIMAL_ACCENT=str(TIDAL))
    _, _, slider = quick_window(world)
    paint_bg(fw, slider, Canvas(fw))
    tone = C.c_int(rgba(ACCENTS[TIDAL][LIGHT])).value
    assert fill_props(world, slider) == {'_ringnav_fill': tone, **{f'style:{s}:fg_color': tone for s in ('normal', 'pressed', 'over', 'focused')}}
    sets = len(fw.calls('widget_set_prop_int'))
    paint_bg(fw, slider, Canvas(fw))
    assert len(fw.calls('widget_set_prop_int')) == sets


def test_classic_s_brightness_clears_stock_s_bar_for_its_own(fw, world):
    theme(fw, CLASSIC)
    _, _, slider = quick_window(world)
    paint_bg(fw, slider, Canvas(fw))
    states = ('normal', 'pressed', 'over', 'focused')
    assert fill_props(world, slider) == {'_ringnav_fill': 1, **{f'style:{s}:fg_color': 0 for s in states},
                                         **{f'style:{s}:bg_color': 0 for s in states}}


def test_another_slider_is_left(fw, world):
    theme(fw, MINIMAL)
    win = world.window('playset_page')
    slider = world.add(win, 'slider_backlight', 'slider')
    paint_bg(fw, slider, Canvas(fw))
    assert fill_props(world, slider) == {}


@pytest.mark.parametrize('text, lines', [('Wi-Fi', ['Wi-Fi']), ('Output Way', ['Output', 'Way']),
                                         ('Low Gain', ['Low Gain']), ('Equalisers', ['Equalise', 'rs']),
                                         ('Sys Settings', ['Sys', 'Settings'])])
def test_a_quick_label_wraps_at_the_last_space_that_fits(fw, world, text, lines):
    """10 px a character here, 80 px a line: a word too long for one breaks where the line is full."""
    theme(fw, CLASSIC, LIGHT='1')
    win = world.window('statusbar_dialog', 'dialog')
    label = world.add(win, 'label_wifi', 'label', text=text)
    canvas = Canvas(fw)
    x, y = 100, 200  # its first line's box
    fw.fn('navigation__quick_label', None, C.c_void_p, C.c_void_p, C.c_int, C.c_int)(canvas.p, label, x, y)
    assert [t for t, *_ in canvas.texts] == lines
    boxes = [r for _, r, *_ in canvas.texts]
    box = world.at(label)
    assert boxes[0][:2] == box[:2] == (x, y) and {(b[0], b[2], b[3]) for b in boxes} == {(x, box[2], boxes[0][3])}
    assert all(10 * len(t) <= r[2] for t, r, *_ in canvas.texts)  # each line fits the column
    assert all(0 < b[1] - a[1] < a[3] for a, b in zip(boxes, boxes[1:]))  # each under the last, tighter than a box
    assert box[1] + box[3] == boxes[-1][1] + boxes[-1][3]  # stock's box round all its lines, for its repaint
    assert {c for _, _, c, _ in canvas.texts} == {0xffffffff}  # white in the light half too: final
    assert world[label].props['style:normal:text_color'] == 0  # its own ink clear
    sets = len(fw.calls('widget_set_prop_int'))
    fw.fn('navigation__quick_label', None, C.c_void_p, C.c_void_p, C.c_int, C.c_int)(canvas.p, label, x, y)
    assert len(fw.calls('widget_set_prop_int')) == sets  # the box once


def test_an_empty_quick_label_draws_nothing(fw, world):
    theme(fw, CLASSIC)
    label = world.add(world.window('statusbar_dialog', 'dialog'), 'label_wifi', 'label', text='')
    canvas = Canvas(fw)
    fw.fn('navigation__quick_label', None, C.c_void_p, C.c_void_p, C.c_int, C.c_int)(canvas.p, label, 100, 200)
    assert canvas.texts == [] and fw.calls('widget_move_resize') == []


class Quick(C.Structure):
    _fields_ = [*((n, C.c_void_p) for n in ('win', 'icon', 'slider', 'pressed')), ('timer', C.c_uint),
                *((n, C.c_int) for n in ('x', 'y', 'sx', 'sy', 'slide'))]


ICONS = ['img_wifiswitch', 'img_btswitch', 'img_lock', 'img_gain', 'img_usbmode', 'img_po', 'img_playset', 'img_sysset']
LABELS = dict(zip(ICONS, ['label_wifi', 'label_bt', 'label_lock', 'label_gain', 'label_usbmode', 'label_outputway',
                          'label_playset', 'label_sysset']))


def quick_page(fw, world, images, light='0', pressed=None, **config):
    theme(fw, CLASSIC, LIGHT=light, **config)
    win = world.window('statusbar_dialog', 'dialog')
    icons = {}
    for i, (name, image) in enumerate(images.items()):
        icons[name] = world.add(win, name, 'image', box=(30 + 80 * (i % 4), 40 + 100 * (i // 4), 60, 60), image=image)
        world.add(win, LABELS[name], 'label', box=(0, 0, 80, 18), text='Label')
    if pressed:
        fw.var('navigation__quick', Quick).pressed = icons[pressed]
    canvas = Canvas(fw)
    paint_bg(fw, win, canvas)
    return win, icons, canvas


def tile(canvas, icon_box, row=30):
    """A quick button's tile, over its icon's box, on one of its rows past the corners: its face's colour (a pixel
    inside its ring) and its ring's (the row under the face)."""
    x, y, w, _ = icon_box
    face = [c for fx, fy, fw_, h, c in canvas.fills if (fx, fy, fw_, h) == (x + 1, y + row, w - 2, 1)]
    ring = [c for fx, fy, fw_, h, c in canvas.fills if (fx, fy, fw_, h) == (x, y + row, w, 1)]
    return face[0], ring[0]


def blue(color):
    r, g, b = channels(rgb_of(color))
    return b > max(r, g)


def test_quick_buttons_on_are_the_accent_s_and_off_the_bar_s_dark(fw, world, stock_h, scheme):
    on, off = stock_h['QS_LIT'][0], 'drop_wifi'
    assert off not in stock_h['QS_LIT']
    _, icons, canvas = quick_page(fw, world, {'img_wifiswitch': on, 'img_btswitch': off})
    (lit, lit_ring), (plain, plain_ring) = tile(canvas, world.at(icons['img_wifiswitch'])), tile(canvas, world.at(icons['img_btswitch']))
    assert lit == lit_ring and blue(lit)  # Classic Blue's blue, flat with no ring
    assert plain == plain_ring == rgba(scheme('bar_line')) != lit  # the dark bar's hairline
    assert canvas.art(f'classic_light_{on}') == [world.at(icons['img_wifiswitch'])[:2] + (20, 10)]  # white on
    assert canvas.art(f'classic_light_{off}_d') == [world.at(icons['img_btswitch'])[:2] + (20, 10)]  # dark off
    assert canvas.fill == 0xff123456


def test_a_light_off_button_is_silver_lighter_at_the_top_in_a_darker_ring(fw, world):
    _, icons, canvas = quick_page(fw, world, {'img_wifiswitch': 'drop_wifi'}, light='1')
    box = world.at(icons['img_wifiswitch'])
    (top, ring), (foot, _) = tile(canvas, box, row=12), tile(canvas, box, row=47)
    assert luma(rgb_of(top)) > luma(rgb_of(foot)) > luma(rgb_of(ring))
    assert canvas.art('classic_light_drop_wifi_l')  # the glyph dark on silver


def test_a_light_on_button_s_ring_is_a_shade_darker(fw, world, stock_h):
    on = stock_h['QS_LIT'][0]
    _, icons, canvas = quick_page(fw, world, {'img_wifiswitch': on}, light='1')
    face, ring = tile(canvas, world.at(icons['img_wifiswitch']))
    assert blue(face) and blue(ring) and luma(rgb_of(ring)) < luma(rgb_of(face))


@pytest.mark.parametrize('accent, ink', [(GRAPHITE, '_l'), (TIDAL, '')])
def test_another_accent_s_on_button_takes_its_red_tone_and_a_glyph_that_shows(fw, world, stock_h, accent, ink):
    on = stock_h['QS_LIT'][0]
    _, icons, canvas = quick_page(fw, world, {'img_wifiswitch': on}, CLASSIC_LIGHT_ACCENT=str(accent))
    assert tile(canvas, world.at(icons['img_wifiswitch'])) == (rgba(ACCENTS[accent][RED]),) * 2
    assert canvas.art(f'classic_light_{on}{ink}')


@pytest.mark.parametrize('image', ['drop_wifi', 'drop_wifiopen'])
def test_a_pressed_quick_button_is_a_sixth_darker(fw, world, image):
    _, icons, canvas = quick_page(fw, world, {'img_wifiswitch': image, 'img_btswitch': image}, pressed='img_wifiswitch')
    pressed, shown = tile(canvas, world.at(icons['img_wifiswitch'])), tile(canvas, world.at(icons['img_btswitch']))
    for a, b in zip(pressed, shown):
        assert channels(rgb_of(a)) == tuple(v * 5 // 6 for v in channels(rgb_of(b)))


def test_each_quick_button_has_its_label_under_it(fw, world):
    _, icons, canvas = quick_page(fw, world, {name: 'drop_wifi' for name in ICONS})
    for name, icon in icons.items():
        x, y, w, h = world.at(icon)
        lx, ly, lw, _ = world.at(world.find(world.top, LABELS[name]))
        assert 2 * lx + lw == 2 * x + w and ly >= y + h  # centred under its icon
    assert sorted(r[:2] for _, r, *_ in canvas.texts) == sorted(world.at(world.find(world.top, LABELS[n]))[:2] for n in icons)


def test_quick_settings_outside_classic_is_stock_s(fw, world):
    theme(fw, MINIMAL)
    win = world.window('statusbar_dialog', 'dialog')
    world.add(win, 'img_wifiswitch', 'image', box=(30, 40, 60, 60), image='drop_wifi')
    canvas = Canvas(fw)
    paint_bg(fw, win, canvas)
    assert canvas.fills == [] and canvas.arts == []


def scrub(fw, world, slider, value):
    """Quick Settings' brightness slider at value, painted after stock's: the canvas."""
    world.set(slider, 'value', value)
    canvas = Canvas(fw)
    fw.fn('navigation__quick_scrub', None, C.c_void_p, C.c_void_p)(slider, canvas.p)
    return canvas


def quick_scrub(fw, world, value, **config):
    theme(fw, CLASSIC, **config)
    return scrub(fw, world, quick_window(world)[2], value)


def hued(f):
    """A fill in a hue: the scrub's fill, over its grey track."""
    r, _, b = channels(rgb_of(f[4]))
    return b - r > 0x30


def track_of(canvas):
    """The scrub's grey track: its x, its width (its corners' soft pixels included) and its rows."""
    grey = [f for f in canvas.fills if not hued(f)]
    x = min(f[0] for f in grey)
    return x, max(f[0] + f[2] for f in grey) - x, {f[1] for f in grey}


def test_the_brightness_knob_travels_the_track_with_the_value(fw, world):
    """It moves with the fill's end: from empty to full the track's width, and a value's share of it."""
    theme(fw, CLASSIC)
    _, _, slider = quick_window(world)
    knobs, tracks = {}, set()
    for value in (0, 25, 50, 100):
        canvas = scrub(fw, world, slider, value)
        x, w, rows = track_of(canvas)
        tracks.add((x, w, min(rows)))
        (knob,) = canvas.art('classic_light_drop_knob')
        knobs[value] = knob[0]
        (image,) = canvas.art('classic_light_drop_track')
        assert image[:2] == (x, min(rows))  # its image over the track
    (_, w, _), = tracks  # the track stays
    assert {v: knobs[v] - knobs[0] for v in knobs} == {v: w * v // 100 for v in knobs}


@pytest.mark.parametrize('value', [5, 6, 50, 100])
def test_the_brightness_fill_shows_from_where_the_knob_no_longer_covers_it(fw, world, value):
    canvas = quick_scrub(fw, world, value)
    x, w, rows = track_of(canvas)
    part = w * value // 100
    fills = [f for f in canvas.fills if hued(f)]
    assert bool(fills) == (part >= 12)  # under 12 px the knob covers it
    assert all(x <= fx and fx + fw_ <= x + part for fx, _, fw_, _, _ in fills)  # from the track's start to the value's share
    assert {y for _, y, *_ in fills} == (rows if fills else set())  # on the track's rows


def test_the_brightness_fill_is_another_accent_s_light_tone(fw, world):
    canvas = quick_scrub(fw, world, 100, CLASSIC_LIGHT_ACCENT=str(TIDAL))
    assert {rgb_of(f[4]) for f in canvas.fills if hued(f)} == {ACCENTS[TIDAL][LIGHT]}


def test_the_brightness_track_is_classic_s_alone(fw, world):
    theme(fw, MINIMAL)
    _, _, slider = quick_window(world)
    canvas = Canvas(fw)
    fw.fn('navigation__quick_scrub', None, C.c_void_p, C.c_void_p)(slider, canvas.p)
    assert canvas.fills == [] and canvas.arts == []


# The bar's title (Classic's label_title) names the top window.

def bar_title(fw, world, st, number=CLASSIC, **config):
    theme(fw, number, **config)
    bar = world.window('system_bar', 'system_bar', box=(0, 0, 375, 26), top=False)
    title = world.add(bar, 'label_title', 'hscroll_label', box=(142, 0, 91, 26))
    st('bar_title', C.c_void_p).value = title
    return title


def sync(fw, win):
    fw.fn('bar_title_sync', None, C.c_void_p)(win)


@pytest.mark.parametrize('name, title', [('playing_page', 'Now Playing'), ('playing_page_stock', 'Now Playing'),
                                         ('coverflow_page', 'Cover Flow'), ('equalizer_page', 'EQ'),
                                         ('spotify_page', 'Spotify'), ('radionp_page', 'Internet Radio'),
                                         ('usbmode_page', 'Q2 Pod')])
def test_a_page_known_by_name_titles_the_bar(fw, world, st, name, title):
    label = bar_title(fw, world, st)
    win = world.window(name)
    world.add(world.add(win, 'view_navbar'), 'label_name', 'hscroll_label', text='Its own label')
    sync(fw, win)
    assert world[label].text == title
    (fn, _, ms), = world.timers
    assert ms == 0 and world.fire(1) == RET_OK and world.invalid == [(label, None)]  # after the paint


def test_search_is_titled_though_a_dialog(fw, world, st):
    label = bar_title(fw, world, st)
    win = world.window('search_dialog', 'dialog')
    st('search_win', C.c_void_p).value = win
    sync(fw, win)
    assert world[label].text == 'Search'


def navbar_page(world, text='Albums', kind='hscroll_label'):
    win = world.window('albums_page')
    nav = world.add(win, 'view_navbar')
    world.add(nav, 'img_back', 'image')
    return win, world.add(nav, 'label_title', kind, text=text)


@pytest.mark.parametrize('kind', ['hscroll_label', 'label'])
def test_a_stock_page_s_navbar_title_titles_the_bar_and_is_followed(fw, world, st, kind):
    label = bar_title(fw, world, st)
    win, own = navbar_page(world, kind=kind)
    sync(fw, win)
    assert world[label].text == 'Albums'
    sync(fw, win)
    assert len(fw.calls('widget_set_text_utf8')) == 1 and len(world.timers) == 1  # unchanged: not written
    world.set_text(own, 'Album Artists')
    sync(fw, win)
    assert world[label].text == 'Album Artists' and len(world.timers) == 2


@pytest.mark.parametrize('text, title', [('Channel Balance', 'Balance'), ('(null)', ''), ('', '')])
def test_a_title_too_long_or_stock_s_null_name(fw, world, st, text, title):
    label = bar_title(fw, world, st)
    world.set_text(label, 'Before')
    win, _ = navbar_page(world, text)
    sync(fw, win)
    assert world[label].text == title


def test_a_payload_page_s_caption_on_show_titles_the_bar(fw, world, st):
    label = bar_title(fw, world, st)
    win = world.window('games_page')
    world.add(win, 'sheet', 'view')
    hidden = world.add(win, 'caption', 'hscroll_label', box=(0, 0, 375, 48), text='Games')
    world[hidden].visible = 0
    world.add(win, 'note', 'hscroll_label', box=(0, 60, 375, 30), text='Not a caption')
    body = world.add(win, 'body', 'view')
    world.add(body, 'caption2', 'hscroll_label', box=(0, 0, 375, 48), text='Tetris')
    sync(fw, win)
    assert world[label].text == 'Tetris'


def test_a_hidden_caption_still_names_the_page(fw, world, st):
    label = bar_title(fw, world, st)
    win = world.window('games_page')
    first = world.add(win, '_page_title', 'hscroll_label', box=(0, 0, 375, 32), text='Games')  # page_title's
    second = world.add(win, 'other', 'hscroll_label', box=(0, 0, 375, 48), text='Second')
    world[first].visible = world[second].visible = 0
    sync(fw, win)
    assert world[label].text == 'Games'


def test_a_dialog_keeps_the_window_s_title(fw, world, st):
    label = bar_title(fw, world, st)
    win, _ = navbar_page(world)
    sync(fw, win)
    dialog = world.window('confirminfo_dialog', 'dialog')
    sync(fw, dialog)
    assert world[label].text == 'Albums'


def test_only_the_top_window_titles_the_bar(fw, world, st):
    label = bar_title(fw, world, st)
    win, _ = navbar_page(world)
    world.window('other_page')  # on top
    sync(fw, win)
    assert world[label].text is None and world.handlers == []


def test_no_title_label_no_title(fw, world, st):
    theme(fw, CLASSIC)
    win, _ = navbar_page(world)
    sync(fw, win)
    assert world.handlers == [] and fw.calls('widget_set_text_utf8') == []


def test_the_window_going_drops_its_title_s_source(fw, world, st):
    label = bar_title(fw, world, st)
    win, own = navbar_page(world)
    sync(fw, win)
    assert st('title_win', C.c_void_p).value == win and st('title_lbl', C.c_void_p).value == own
    for gone in world.on(win, EVT_DESTROY):
        gone()
    assert st('title_win', C.c_void_p).value is None and st('title_lbl', C.c_void_p).value is None
    assert {a[0] for _, a in fw.calls('widget_off_by_func')} == {win, own}
    world.set_text(own, 'Songs')
    sync(fw, win)  # the same window again: found afresh
    assert world[label].text == 'Songs'


def test_home_s_own_list_is_q2_pod(fw, world, st):
    label = bar_title(fw, world, st)
    sync(fw, world.window('home_page'))
    assert world[label].text == 'Q2 Pod'
    win, _ = navbar_page(world)
    sync(fw, win)
    assert world[label].text == 'Albums'


def test_a_dark_page_s_title_is_the_dark_bar_s(fw, world, st):
    label = bar_title(fw, world, st)
    st('bar_dark', C.c_ubyte).value = 1
    y = world.at(label)[1]
    win, _ = navbar_page(world)
    sync(fw, win)
    _, _, bar_w, bar_h = world.at(world[label].parent)
    assert world.at(label) == (0, y, bar_w, bar_h - 1)  # across the bar over its line, as the dark bar's title
    assert world[label].props['style:normal:text_align_h'] == 'center'


def classic_geom(fw, st):
    """The set Classic boots with, as theme_boot would leave it."""
    st('geom').value = fw.fn('theme_geom', C.c_int, C.c_void_p)(fw.fn('theme', C.c_void_p)())


def test_songs_action_bar_is_found_with_the_title(fw, world, st):
    bar_title(fw, world, st)
    classic_geom(fw, st)
    win, _ = navbar_page(world)
    action = world.add(win, 'view_navbar_allplay')
    sync(fw, win)
    assert st('action_view', C.c_void_p).value == action


def action_view(world, n):
    view = world.add(world.window('songs_page'), 'view_navbar_allplay', box=(0, 0, 375, 40))
    made = [lambda: world.add(view, 'label', 'label', box=(40, 0, 200, 40), text='Play All'),
            lambda: world.add(view, 'play', 'image', box=(0, 0, 40, 40), style='s_img_playall'),
            lambda: world.add(view, 'sort', 'image', box=(335, 0, 40, 40), style='s_img_sort')]
    return view, [make() for make in made[:n]]


def test_songs_action_bar_is_a_plain_row(fw, world, st):
    """The set's action_row high (Classic's theme.xml); the label at the list's text x, over Play All's left part
    of the row, Sort's bars to their right, inside the row."""
    theme(fw, CLASSIC)
    classic_geom(fw, st)
    fw.mock('image_set_draw_type', lambda w, t, *_: world.set(w, 'draw_type', t))
    view, (label, play, sort) = action_view(world, 3)
    fw.fn('navigation__action_row', None, C.c_void_p)(view)
    h = GEOMETRY['action_row']
    (lx, ly, lw, lh), (px_, py, pw, ph), (sx, sy, sw, sh) = world.at(label), world.at(play), world.at(sort)
    assert (ly, lh) == (py, ph) == (sy, sh) == (0, h) and lx == GEOMETRY['text_x']
    assert px_ == 0 and px_ < lx < lx + lw <= sx and pw < sx and sx + sw <= world.at(view)[2]  # side by side
    assert world[play].props['style'] == world[sort].props['style'] == 'default'
    assert fw.calls('image_base_set_image') and world[sort].props['draw_type'] == O['IMAGE_DRAW_CENTER']
    moves = len(fw.calls('widget_move_resize'))
    fw.fn('navigation__action_row', None, C.c_void_p)(view)
    assert len(fw.calls('widget_move_resize')) == moves  # done: its first child's height says so


@pytest.mark.parametrize('n, moved', [(1, 0), (2, 2)])
def test_an_action_bar_without_sort_or_play(fw, world, st, n, moved):
    theme(fw, CLASSIC)
    classic_geom(fw, st)
    view, _ = action_view(world, n)
    fw.fn('navigation__action_row', None, C.c_void_p)(view)
    assert len(fw.calls('widget_move_resize')) == moved


def test_the_action_bar_has_the_rows_lines_under_it(fw, world, st):
    theme(fw, CLASSIC)
    view, _ = action_view(world, 2)
    st('action_view', C.c_void_p).value = view
    canvas = Canvas(fw)
    paint_bg(fw, view, canvas)
    lines = {y: c for x, y, w, h, c in canvas.fills if (x, w, h) == (0, 375, 1)}
    assert lines and max(lines) == 39 and sorted(lines) == list(range(40 - len(lines), 40))
    assert canvas.fill == 0xff123456


def test_a_bar_title_measures_on_the_bar_s_canvas(fw, world, st, theme_t):
    label = bar_title(fw, world, st)
    canvas = Canvas(fw)
    fw.mock('widget_get_canvas', lambda w, *_: canvas.p if w == label else 0)
    fits = fw.fn('bar_title_fits', C.c_int, C.c_void_p)
    page = world.window('x_page')
    short, long_ = world.add(page, 'a', 'label', text='Settings'), world.add(page, 'b', 'label', text='Playlists')
    assert fits(short) == 1 and fits(long_) == 0  # 91 px less 2 px each side: 8 of these characters
    assert canvas.font[1] == theme_t('font_px', C.c_ubyte, ROLE_BAR)  # at the bar's size


def test_no_bar_title_fits_nothing(fw, world, st):
    theme(fw, CLASSIC)
    page = world.window('x_page')
    assert fw.fn('bar_title_fits', C.c_int, C.c_void_p)(world.add(page, 'a', 'label', text='A')) == 0


def test_an_empty_page_keeps_its_message_and_the_bar_its_name(fw, world, st):
    label = bar_title(fw, world, st)
    win = world.window('songs_page')
    body = world.add(win, 'body', 'view')
    caption = world.add(body, 'caption', 'hscroll_label', box=(0, 0, 375, 48), text='No Songs')
    fw.fn('page_empty', None, C.c_void_p, C.c_char_p)(body, b'Songs')
    assert world[caption].text == 'Songs' and world[caption].visible == 0
    message = world[body].children[-1]
    assert world[message].text == 'No Songs' and world[message].name == '' and world[message].visible
    assert world[label].text == 'Songs'


def test_a_theme_with_in_page_titles_leaves_an_empty_page(fw, world, st):
    theme(fw, MINIMAL)
    body = world.add(world.window('songs_page'), 'body', 'view')
    caption = world.add(body, 'caption', 'hscroll_label', box=(0, 0, 375, 48), text='No Songs')
    fw.fn('page_empty', None, C.c_void_p, C.c_char_p)(body, b'Songs')
    assert world[caption].text == 'No Songs' and len(world[body].children) == 1


# The background hook's own windows.

def system_bar(fw, world, **props):
    bar = world.window('system_bar', 'system_bar', box=(0, 0, 375, 26), top=False, **props)
    fw.var('fw_ctx_system_bar', C.c_void_p).value = bar
    return bar


@pytest.mark.parametrize('number, light, color', [(MINIMAL, '0', 0x000000), (MINIMAL, '1', PAPER * 0x010101),
                                                  (MODERN, '0', 0x000000)])
def test_a_flat_bar_is_black_or_its_light_half(fw, world, number, light, color):
    theme(fw, number, LIGHT=light)
    bar = system_bar(fw, world)
    canvas = Canvas(fw)
    paint_bg(fw, bar, canvas)
    assert canvas.fills[0] == (0, 0, 375, 26, rgba(color)) and canvas.fill == 0xff123456


def test_modern_s_light_bar_is_an_ipod_s_grey(fw, world, scheme):
    theme(fw, MODERN, LIGHT='1')
    bar = system_bar(fw, world)
    canvas = Canvas(fw)
    paint_bg(fw, bar, canvas)
    assert canvas.fills[0] == (0, 0, 375, 26, rgba(scheme('bar_light'))) and scheme('bar_light')


@pytest.mark.parametrize('light', ['0', '1'])
def test_classic_s_bar_is_its_gloss_over_its_line(fw, world, scheme, light):
    theme(fw, CLASSIC, LIGHT=light)
    bar = system_bar(fw, world)
    canvas = Canvas(fw)
    paint_bg(fw, bar, canvas)
    rows = canvas.rows(x=0, w=375)
    assert sorted(rows) == list(range(26))
    assert rows[25] == rgba(scheme('bar_line'))
    if light == '1':
        assert rows[24] == rgba(scheme('bar_line2')) and luma(rgb_of(rows[0])) > luma(rgb_of(rows[23]))  # lit from above
    assert canvas.fill == 0xff123456


def test_a_dark_page_s_bar_is_the_dark_bar(fw, world, st, scheme):
    theme(fw, CLASSIC, LIGHT='1')
    st('bar_dark', C.c_ubyte).value = 1
    bar = system_bar(fw, world)
    canvas = Canvas(fw)
    paint_bg(fw, bar, canvas)
    rows = canvas.rows(x=0, w=375)
    assert sorted(rows) == list(range(26)) and rows[25] != rgba(scheme('bar_line'))  # the dark half's line


LOOKS = {'1': MINIMAL, '2': MODERN, '3': CLASSIC}  # Status Bar's values, the picker's order


@pytest.mark.parametrize('number, look', [(t, v) for t in LOOKS.values() for v in LOOKS if LOOKS[v] != t])
def test_status_bar_paints_the_bar_of_the_theme_it_names(fw, world, number, look):
    bar = system_bar(fw, world)

    def painted(t, **keys):
        theme(fw, t, **keys)
        canvas = Canvas(fw)
        paint_bg(fw, bar, canvas)
        assert canvas.fill == 0xff123456
        return canvas.fills
    assert painted(number, STATUSBAR=look) == painted(LOOKS[look])


def test_status_bar_at_theme_is_the_theme_s_own(fw, world):
    bar = system_bar(fw, world)

    def painted(**keys):
        theme(fw, CLASSIC, **keys)
        canvas = Canvas(fw)
        paint_bg(fw, bar, canvas)
        return canvas.fills
    assert painted(STATUSBAR='0') == painted()


def test_status_bar_goes_by_the_light_half_of_the_theme_shown(fw, world, scheme):
    theme(fw, MINIMAL, LIGHT='1', STATUSBAR='3')  # Classic's gloss on Minimal's Light Mode: its light rows
    bar = system_bar(fw, world)
    canvas = Canvas(fw)
    paint_bg(fw, bar, canvas)
    rows = canvas.rows(x=0, w=375)
    assert sorted(rows) == list(range(26)) and luma(rgb_of(rows[0])) > luma(rgb_of(rows[23]))


@pytest.mark.parametrize('look', ['1', '2'])
def test_a_dark_page_under_a_flat_status_bar_is_plain_black(fw, world, st, look):
    theme(fw, CLASSIC, LIGHT='1', STATUSBAR=look)
    st('bar_dark', C.c_ubyte).value = 1
    bar = system_bar(fw, world)
    canvas = Canvas(fw)
    paint_bg(fw, bar, canvas)
    assert canvas.fills[0] == (0, 0, 375, 26, rgba(0))
    assert canvas.fill == 0xff123456


def test_painting_the_bar_keeps_its_clock_and_title_current(fw, world, st):
    theme(fw, CLASSIC)
    bar = system_bar(fw, world)
    title = world.add(bar, 'label_title', 'hscroll_label', box=(142, 0, 91, 26))
    st('bar_title', C.c_void_p).value = title
    win, _ = navbar_page(world)
    paint_bg(fw, bar, Canvas(fw))
    assert world[title].text == 'Albums'
    assert st('clock_timer', C.c_uint).value and len(world.timers) >= 2  # the clock's, at once, and the title's
    timers = len(world.timers)
    paint_bg(fw, win, Canvas(fw))  # the top window: the same, but the clock is armed
    assert len(world.timers) == timers


def test_home_keeps_its_title_while_music_plays_and_the_state_sits_by_the_battery(fw, world, st):
    """Home's own list is like any page for the play state: the bar's paint shows it by the battery, as over a
    page, and repaints the bar after it; 'Q2 Pod' stays shown."""
    theme(fw, CLASSIC)
    bar = system_bar(fw, world)
    title = world.add(bar, 'label_title', 'hscroll_label', box=(142, 0, 91, 26))
    state = world.add(bar, 'img_state', 'image', box=(250, 0, 18, 26))
    world[state].visible = 0
    fw.mock('image_base_set_image', lambda w, s, *_: world.set(w, 'image', C.string_at(s).decode()))
    status = [2]  # playing
    fw.mock('mclGetPlayStatus', lambda *_: status[0])

    def shown():
        """After the bar's paint: the title, whether it shows, and the state's image when it shows."""
        paint_bg(fw, bar, Canvas(fw))
        return world[title].text, world[title].visible, world[state].visible and world[state].props.get('image')

    home = world.window('home_page')
    on_home = shown()
    assert on_home[:2] == ('Q2 Pod', 1) and on_home[2]
    assert st('bar_timer', C.c_uint).value  # mid-paint: the bar again after this frame
    navbar_page(world)
    assert shown() == ('Albums', 1, on_home[2])  # over a page: the same image in the same img_state
    world.top = home
    status[0] = 0  # stopped
    assert shown() == ('Q2 Pod', 1, 0)


def field_image(canvas):
    """The field's image (paint_field_plain: 15 px caps round a stretched column, the row under it included):
    its box, once its caps meet the column."""
    left, mid, right = canvas.art('classic_light_field')
    assert left[2] == right[2] == 15 and left[0] + left[2] == mid[0] and mid[0] + mid[2] == right[0]
    assert left[1] == mid[1] == right[1] and left[3] == mid[3] == right[3]
    return left[0], left[1], right[0] + right[2] - left[0], left[3]


def inside(r, box):
    x, y, w, h = r
    bx, by, bw, bh = box
    return bx <= x and by <= y and x + w <= bx + bw and y + h <= by + bh


def test_the_t9_page_has_the_dark_strip_and_its_field(fw, world, st):
    """A search box alone on a page (t9_box): the dark strip from the top round the field the edit is in, and the
    chevron where its tap is."""
    theme(fw, CLASSIC)
    page = world.window('t9_page')
    edit = world.add(page, 'edit', 'edit', box=(0, 0, 300, 30))
    fw.fn('t9_box', None, C.c_void_p, C.c_void_p, C.c_char_p)(page, edit, b'Search Songs')
    canvas = Canvas(fw)
    paint_bg(fw, page, canvas)
    rows = sorted(canvas.rows(x=0, w=375))
    x, y, w, h = field_image(canvas)
    ex, ey, ew, eh = world.at(edit)
    assert rows == list(range(rows[-1] + 1)) and y + h <= len(rows)  # from the page's top, past the field
    assert (x, w) == (ex, ew) and inside((ex, ey, ew, eh), (x, y, w, h))  # the typed text in the drawn field
    (back,) = canvas.art('classic_light_kb_back')
    assert inside(back, world.at(st('t9_back', C.c_void_p).value)) and back[0] + back[2] <= x  # left of it
    assert canvas.fill == 0xff123456


def test_the_keyboard_is_a_ramp_darkening_down_its_window(fw, world, st):
    theme(fw, CLASSIC)
    kb = world.window('kb_default_t9', box=(0, 60, 375, 260))
    st('kb_win', C.c_void_p).value = kb
    canvas = Canvas(fw)
    paint_bg(fw, kb, canvas)
    rows = canvas.rows(x=0, w=375)
    assert sorted(rows) == list(range(260))  # screen rows 60 to 319, in the window's
    shades = [luma(rgb_of(rows[y])) for y in range(260)]
    assert shades == sorted(shades, reverse=True) and shades[0] > shades[-1]


def test_the_rating_box_is_classic_s_panel(fw, world, st):
    theme(fw, CLASSIC)
    box = world.add(world.window('rate_dialog', 'dialog'), 'box', 'view', box=(30, 100, 320, 126))
    st('rate_box', C.c_void_p).value = box
    canvas = Canvas(fw)
    paint_bg(fw, box, canvas)
    (shadow,) = canvas.art('classic_light_rate_shadow')
    pad, drop = 8, 2  # the shadow image reaches PANEL_PAD past its box, PANEL_DROP below the panel
    panel = (pad, pad - drop, 304, 110)
    assert shadow[:2] == (panel[0] - pad, panel[1] - pad + drop) == (0, 0)  # the box's own corner
    ys = {y for x, y, w, h, c in canvas.fills}
    assert min(ys) == panel[1] - 1 and max(ys) == panel[1] + panel[3]  # a ring a pixel outside it
    assert canvas.fill == 0xff123456


# The volume (stock's dialog/volume_dialog over another window).

def volume(fw, world, st, level=37, maximum=100, under='home_page', np=False, beside=False):
    """The dialog over a window (Now Playing's with np): (dialog, slider_vol, label_vol)."""
    below = world.window(under, top=False)
    if np:
        slider = world.add(below, 'slider_play', 'slider', box=(66, 245, 243, 20), bar_size=4)
        times = world.add(below, 'label_playtime', 'label', box=(16, 247 if beside else 270, 46, 16))
        remain = world.add(below, 'label_ipod_remain', 'label', box=(313, 247, 46, 16))
        st('np_win', C.c_void_p).value = below
        st('np_slider', C.c_void_p).value = slider
        st('np_elapsed', C.c_void_p).value = times
        st('np_remain', C.c_void_p).value = remain if beside else None
    dialog = world.window('volume_dialog', 'dialog')
    vol = world.add(dialog, 'slider_vol', 'slider', box=(0, 100, 375, 40), max=maximum, value=level)
    label = world.add(dialog, 'label_vol', 'label', box=(0, 150, 375, 40))
    return dialog, vol, label


def vol_paint(fw, dialog, canvas):
    fw.fn('vol_paint', None, C.c_void_p, C.c_void_p)(dialog, canvas.p)


@pytest.mark.parametrize('level, fill', [(37, 231 * 37 // 100), (100, 231), (1, 10), (0, None)])
def test_away_from_now_playing_the_volume_is_a_pill_and_its_number_on_a_panel(fw, world, st, level, fill):
    theme(fw, MINIMAL)
    dialog, vol, label = volume(fw, world, st, level)
    canvas = Canvas(fw)
    vol_paint(fw, dialog, canvas)
    x, y, w, h = O['VOL_PANEL_X'], O['VOL_PANEL_Y'], 375 - 2 * O['VOL_PANEL_X'], O['VOL_PANEL_H']
    pad, bar, room = O['VOL_PANEL_PAD'], O['VOL_PANEL_BAR'], O['VOL_PANEL_TEXT']
    track = (x + pad, y + (h - bar) // 2, w - 2 * pad - room, bar)
    assert canvas.rounded[0] == ((x, y, w, h), O['LETTER_ALPHA'] << 24 | O['FILL_RGB'], O['LETTER_RADIUS'])
    assert canvas.rounded[1] == (track, rgba(O['VOL_PANEL_TRACK']), bar // 2)
    assert canvas.rounded[2:] == ([((track[0], track[1], fill, bar), rgba(0xffffff), bar // 2)] if fill else [])
    assert [(t, r) for t, r, *_ in canvas.texts] == [(str(level), (track[0] + track[2], y, room, h))]
    assert world.at(vol) == (x, y, w, h) and world[vol].visible == world[label].visible == 0  # stock's under it
    assert canvas.fill == 0xff123456


def test_the_volume_with_no_range_is_empty(fw, world, st):
    theme(fw, MINIMAL)
    dialog, *_ = volume(fw, world, st, 5, maximum=0)
    canvas = Canvas(fw)
    vol_paint(fw, dialog, canvas)
    assert len(canvas.rounded) == 2 and canvas.texts[0][0] == '5'


def added(after, before):
    """after's fills less before's, each as many times as before has it."""
    extra = list(after.fills)
    for f in before.fills:
        extra.remove(f)
    return extra


def volume_bar(fw, world, dialog, vol, level):
    """Classic's panel at no volume and at level: (the rows the level added: x, width, rows; the track's width
    on those rows; the canvas at level)."""
    world.set(vol, 'value', 0)
    silent = Canvas(fw)
    vol_paint(fw, dialog, silent)
    world.set(vol, 'value', level)
    canvas = Canvas(fw)
    vol_paint(fw, dialog, canvas)
    lit = added(canvas, silent)
    (x, w), = {(f[0], f[2]) for f in lit}
    rows = {f[1] for f in lit}
    (track,) = {f[2] for f in silent.fills if f[0] == x and f[1] in rows}
    return (x, w, rows), track, canvas


@pytest.mark.parametrize('light, ink', [('0', 'd'), ('1', 'l')])
def test_classic_s_volume_is_its_bar_between_two_speakers_and_no_number(fw, world, st, light, ink):
    theme(fw, CLASSIC, LIGHT=light)
    dialog, vol, _ = volume(fw, world, st)
    (_, full, _), track, _ = volume_bar(fw, world, dialog, vol, 100)
    (x, w, rows), track, canvas = volume_bar(fw, world, dialog, vol, 60)
    assert full == track and w == track * 60 // 100  # the level's share of the track, from its start
    quiet, loud = canvas.art(f'classic_light_spk_quiet_{ink}'), canvas.art(f'classic_light_spk_loud_{ink}')
    assert quiet[0][0] < x and loud[0][0] >= x + track  # each speaker outside the bar
    assert canvas.texts == [] and canvas.art('classic_light_panel_shadow')


def test_over_now_playing_the_volume_takes_the_times_band(fw, world, st):
    theme(fw, MINIMAL)
    dialog, *_ = volume(fw, world, st, 37, under='playing_page', np=True)
    canvas = Canvas(fw)
    vol_paint(fw, dialog, canvas)
    band = (0, 245, 375, 270 + 16 - 245)  # from the progress bar's top to the times' foot
    assert (*band, 0xff000000) in canvas.fills
    bar = (66, 245 + (20 - 4) // 2, 243, 4)
    assert canvas.rounded[0] == (bar, rgba(O['TRACK_COLOR']), 2)
    assert canvas.rounded[1][0] == (66, bar[1], 243 * 37 // 100, 4)
    assert [(t, r, f[1]) for t, r, _, f in canvas.texts] == [('Volume 37', (0, 270, 375, 16), O['NP_TIMES_PX'])]


@pytest.mark.parametrize('level', [20, 80])
def test_over_classic_s_now_playing_speakers_take_the_times_places(fw, world, st, level):
    theme(fw, CLASSIC)
    dialog, *_ = volume(fw, world, st, level, under='playing_page', np=True, beside=True)
    canvas = Canvas(fw)
    vol_paint(fw, dialog, canvas)
    assert (16, 247, 46, 16, 0xff000000) in canvas.fills and (313, 247, 46, 16, 0xff000000) in canvas.fills
    (lo,), (hi,) = canvas.art('classic_light_vol_lo'), canvas.art('classic_light_vol_hi')
    assert lo[0] + lo[2] == 16 + 46 and hi[0] == 313  # ending where the elapsed time ended, from the remaining's
    top = 245 + (20 - 4) // 2  # the bar's first row
    assert abs((2 * lo[1] + lo[3]) - (2 * top + 4)) <= 1 and lo[1] == hi[1]  # centred on the bar's rows
    starts = {x for x, y, w, h, c in canvas.fills if h == 1 and y >= top and x > 66}
    assert 66 + 243 * level // 100 in starts  # the track resumes where the volume ends
    assert canvas.texts == []


def test_over_classic_s_now_playing_the_volume_s_percentage_takes_the_page_dots_place(fw, world, st, vd):
    theme(fw, CLASSIC)
    dialog, vol, _ = volume(fw, world, st, 20, maximum=80, under='playing_page', np=True, beside=True)
    page = st('np_win', C.c_void_p).value
    column = world.add(page, 'slide_view_view', box=(0, 8, 375, 200))
    dots = world.add(column, 'slide_indicator1', 'slide_indicator', box=(0, 182, 375, 10))
    canvas = Canvas(fw)
    vol_paint(fw, dialog, canvas)
    assert not world[dots].visible and canvas.texts == []  # hidden first: the number is the next paint's
    assert world.fire(1) == RET_REPEAT and world.invalid == [(dialog, None)]
    canvas = Canvas(fw)
    vol_paint(fw, dialog, canvas)
    px = O['VOL_PCT_PX']
    assert px > O['NP_TIMES_PX']  # larger than the times it sits above
    assert [(t, r, f[1]) for t, r, _, f in canvas.texts] == [('25%', (0, 8 + 182 + 5 - px // 2 - 2, 375, px + 4), px)]
    world.invalid.clear()
    assert world.fire(1) == RET_REPEAT and world.invalid == [] and not world[dots].visible
    world.set(vol, 'value', 40)  # a repaint of the bar's rows alone draws the bar at 50%, the number clipped away
    canvas = Canvas(fw)
    canvas.clip = (0, 240, 375, 30)
    vol_paint(fw, dialog, canvas)
    assert world.fire(1) == RET_REPEAT and world.invalid == [(dialog, None)]  # so the poll repaints it whole
    vol_paint(fw, dialog, Canvas(fw))
    world.invalid.clear()
    assert world.fire(1) == RET_REPEAT and world.invalid == []
    world.window('home_page')  # the dialog gone: the dots are back and their page repainted
    assert world.fire(1) == RET_OK and world[dots].visible and world.invalid == [(page, None)]
    assert vd('vol_dots', C.c_void_p).value is None


def test_dots_hidden_by_another_or_a_volume_without_its_poll_keep_the_dots(fw, world, st, vd):
    theme(fw, CLASSIC)
    dialog, *_ = volume(fw, world, st, 20, under='playing_page', np=True, beside=True)
    dots = world.add(st('np_win', C.c_void_p).value, 'slide_indicator1', 'slide_indicator', box=(0, 182, 375, 10))
    fw.mock('timer_add', lambda *_: 0)  # no poll to bring them back
    for _ in range(2):
        canvas = Canvas(fw)
        vol_paint(fw, dialog, canvas)
        assert world[dots].visible and canvas.texts == []
    vd('vol_timer', C.c_uint).value = 1
    world[dots].visible = 0
    for _ in range(2):
        canvas = Canvas(fw)
        vol_paint(fw, dialog, canvas)
        assert canvas.texts == [] and vd('vol_dots', C.c_void_p).value is None


def test_the_volume_over_another_window_gives_the_dots_back(fw, world, st, vd):
    theme(fw, CLASSIC)
    dialog, *_ = volume(fw, world, st, 20, under='playing_page', np=True, beside=True)
    page = st('np_win', C.c_void_p).value
    dots = world.add(page, 'slide_indicator1', 'slide_indicator', box=(0, 182, 375, 10))
    vol_paint(fw, dialog, Canvas(fw))
    assert not world[dots].visible
    world.window('home_page')
    again = world.window('volume_dialog', 'dialog')  # reopened over Home before the poll saw the first close
    world.add(again, 'slider_vol', 'slider', box=(0, 100, 375, 40), max=100, value=20)
    vol_paint(fw, again, Canvas(fw))
    assert world[dots].visible and vd('vol_dots', C.c_void_p).value is None


def test_the_volume_polls_while_it_is_on_top(fw, world, st, vd):
    theme(fw, CLASSIC)
    dialog, vol, _ = volume(fw, world, st, 37)
    vol_paint(fw, dialog, Canvas(fw))
    vol_paint(fw, dialog, Canvas(fw))
    (fn, _, ms), = world.timers
    assert ms == O['VOL_POLL_MS']  # one, however many paints
    assert world.fire(1) == RET_REPEAT and world.invalid == []
    world.set(vol, 'value', 38)
    assert world.fire(1) == RET_REPEAT and world.invalid == [(dialog, None)]  # a change repaints it whole
    world.window('home_page')
    assert world.fire(1) == RET_OK and vd('vol_dialog', C.c_void_p).value is None and vd('vol_timer', C.c_uint).value == 0


@pytest.mark.parametrize('name, below', [('home_page', 1), ('volume_dialog', 0)])
def test_no_volume_dialog_no_volume(fw, world, st, name, below):
    theme(fw, MINIMAL)
    if below:
        world.window('below')
    dialog = world.window(name, 'dialog')
    world.add(dialog, 'slider_vol', 'slider', value=50)
    canvas = Canvas(fw)
    vol_paint(fw, dialog, canvas)
    assert canvas.rounded == [] and world.timers == []


def test_the_bar_s_paint_with_the_volume_on_top_draws_it(fw, world, st):
    """The dialog painted as the top window draws all that vol_paint draws over it."""
    theme(fw, CLASSIC)
    system_bar(fw, world)
    dialog, *_ = volume(fw, world, st, 50)
    canvas = Canvas(fw)
    paint_bg(fw, dialog, canvas)
    alone = Canvas(fw)
    vol_paint(fw, dialog, alone)
    assert alone.fills and alone.arts
    assert collections.Counter(alone.fills) <= collections.Counter(canvas.fills)
    assert collections.Counter(alone.arts) <= collections.Counter(canvas.arts)


def test_the_volume_panel_s_colour_is_defined(fw, world, st):
    """The panel away from Now Playing: its fill is the fast-scroll letter's, 85% of the dark surface."""
    theme(fw, MODERN)
    dialog, *_ = volume(fw, world, st, 50)
    canvas = Canvas(fw)
    vol_paint(fw, dialog, canvas)
    assert canvas.rounded[0][1] == O['LETTER_ALPHA'] << 24 | O['FILL_RGB']


def test_classic_dresses_its_own_confirm(fw, world):
    """Classic's pop-up is its own form: the plain prompt's layout leaves it."""
    theme(fw, CLASSIC)
    win, shade, prompt, *_ = confirm(world)
    paint_bg(fw, win, Canvas(fw))
    assert world.at(prompt) == (30, 60, 315, 40)


def test_a_keyboard_key_is_classic_s_own_not_stock_s(fw, world, st):
    theme(fw, CLASSIC)
    kb = world.window('kb_default_t9')
    key = world.add(world.add(world.add(world.add(kb, 'panel'), 'page'), 'row'), '2', 'button', box=(0, 0, 60, 40), text='abc')
    st('kb_win', C.c_void_p).value = kb
    canvas = Canvas(fw)
    assert paint_bg(fw, key, canvas, stock=5) == 0
    assert fw.calls('stock_paint_bg_trampoline') == [] and [t for t, *_ in canvas.texts][-1] == 'abc'


# Classic's Search: stock's dialog laid out as it loads, then painted.

TABS = ['btn_track', 'btn_artist', 'btn_album', 'btn_genre']
LISTS = ['scroll_view_track', 'scroll_view_artist', 'scroll_view_album', 'scroll_view_genre']
MESSAGES = ['label_track_msg', 'label_artist_msg', 'label_album_msg', 'label_genre_msg', 'label_tip']


def ui_load(fw, st, name, root, parent=0x4000):
    """ringnav_ui_load over stock's loader, which builds root: its answer and the names stock was asked for."""
    st('theme_booted').value = 1  # the system bar's load booted the theme already
    asked = []
    fw.mock('stock_ui_load_trampoline', lambda n, p, *_: asked.append((C.string_at(n).decode(), p)) or root)
    got = fw.fn('ringnav_ui_load', C.c_void_p, C.c_char_p, C.c_void_p)(name, parent)
    assert asked and asked[0][1] == parent
    return got or 0, [n for n, _ in asked]


def search_window(world, results=True, missing=()):
    win = world.window('search_dialog', 'dialog')
    world.add(win, 'view_navbar', box=(0, 0, 375, 64))
    item = world.add(win, 'list_item_search', 'list_item', box=(0, 20, 300, 30))
    world.add(item, 'search_edit', 'edit', box=(0, 0, 300, 30), text='')
    world.add(item, 'img_search', 'image', box=(0, 0, 30, 30))
    world.add(win, 'img_return', 'image', box=(0, 0, 60, 60))
    control = world.add(win, 'tab_control', 'tab_control', box=(0, 100, 375, 220))
    group = world.add(control, 'tab_button_group', 'tab_button_group', box=(0, 0, 375, 40))
    for i, name in enumerate(TABS):
        world.add(group, name, 'tab_button', box=(90 * i, 0, 90, 40), text=name[4:])
    pages = world.add(control, 'pages', 'pages', box=(0, 40, 375, 180))
    for name in ('view_album', 'view_genre'):
        world.add(pages, name, box=(0, 0, 375, 100))
    for name in LISTS:
        world.add(world.add(pages, 'lv_' + name, 'list_view'), name, 'scroll_view')
    for name in MESSAGES:
        world.add(pages, name, 'label')
    history = world.add(win, 'view', box=(0, 64, 375, 256))
    world.add(history, 'list_view', 'list_view', box=(0, 50, 375, 206))
    world.add(win, 'view_tip', box=(0, 64, 375, 256))
    world[control].visible = results
    for name in missing:
        world[world.find(win, name)].name = 'gone'
    return win


def named(world, win, name):
    return world.find(win, name)


def keyboard_up(fw, world, st):
    """T9's window opening over the page under it, from screen row 60 (kb_ramp's)."""
    kb = world.window('kb_default_t9', box=(0, 60, 375, 260), top=False)
    ui_load(fw, st, b'kb_default_t9', kb)
    return kb


def search_paint(fw, world, st, results=True, keyboard=False, typed=''):
    """Classic's Search loaded, with results or the history, the keyboard down or up, then painted: (window, the
    system bar, the canvas)."""
    theme(fw, CLASSIC)
    bar = system_bar(fw, world)
    win = search_window(world, results)
    ui_load(fw, st, b'search_dialog', win)
    world.set_text(named(world, win, 'search_edit'), typed)
    if keyboard:
        keyboard_up(fw, world, st)
    canvas = Canvas(fw)
    fw.mock('widget_paint', lambda w, c, *_: canvas.arts.append(('widget_paint', w)))
    paint_bg(fw, win, canvas)
    return win, bar, canvas


def test_search_s_tabs_split_the_field_s_width_under_it_with_no_gap(fw, world, st):
    win, _, canvas = search_paint(fw, world, st)
    x, y, w, h = field_image(canvas)
    control, group = world.at(named(world, win, 'tab_control')), world.at(named(world, win, 'tab_button_group'))
    assert (control[0] + group[0], group[2]) == (x, w) and control[1] + group[1] > y + h  # as wide as it, under it
    boxes = [world.at(named(world, win, n)) for n in TABS]
    assert [b[0] for b in boxes] == [0] + [x + w for x, _, w, _ in boxes[:-1]]  # each starts where the last ends
    assert boxes[-1][0] + boxes[-1][2] == group[2] and max(b[2] for b in boxes) - min(b[2] for b in boxes) <= 1
    assert {(b[1], b[3]) for b in boxes} == {(0, group[3])}  # each the group's height
    for name in TABS:
        props = world[named(world, win, name)].props
        bg = {k: v for k, v in props.items() if k.endswith(':bg_color')}
        assert len(bg) == 10 and set(bg.values()) == {0}  # the painter draws the track: stock's tab clear
        assert {v for k, v in props.items() if k.endswith(':font_name')} == {'classic-light'}


def test_search_s_tabs_pages_fill_the_control_under_them(fw, world, st):
    win, _, canvas = search_paint(fw, world, st)
    _, y, _, h = field_image(canvas)
    W, H = world.at(win)[2:]
    control, group = world.at(named(world, win, 'tab_control')), world.at(named(world, win, 'tab_button_group'))
    pages = world.at(named(world, win, 'pages'))
    assert control == (0, y + h, W, H - y - h)  # under the field, whose taps stock binds, to the screen's foot
    assert pages[1] >= group[1] + group[3] and (pages[0], pages[2], pages[1] + pages[3]) == (0, control[2], control[3])
    (_, (target, *params)), = fw.calls('widget_set_self_layout_params')
    assert target == named(world, win, 'pages')
    assert [int(C.string_at(p)) for p in params[:4]] == list(pages)  # a relayout keeps them there
    for name in ('view_album', 'view_genre'):
        assert world.at(named(world, win, name)) == (0, 0, pages[2], pages[3])


def test_search_s_history_starts_where_the_field_s_strip_ends(fw, world, st):
    """The history and its tip show while the tabs don't, so they sit in their place, right under the strip."""
    win, _, canvas = search_paint(fw, world, st, results=False)
    end = max(canvas.rows(x=0, w=375)) + 1
    W, H = world.at(win)[2:]
    history, tip = world.at(named(world, win, 'view')), world.at(named(world, win, 'view_tip'))
    assert history == tip == (0, end, W, H - end)
    rows = world.at(named(world, win, 'list_view'))
    assert history[1] + rows[1] + rows[3] == H  # its list, under its header, ends at the screen's foot


def test_search_s_field_is_the_drawn_field_and_its_rows_the_set_s(fw, world, st):
    win, _, canvas = search_paint(fw, world, st)
    x, y, w, h = field_image(canvas)
    item = world.at(named(world, win, 'list_item_search'))
    assert item[:3] == (x, y, w) and h == item[3] + 1  # the image's row under it included
    assert world.at(named(world, win, 'search_edit')) == (0, 0, item[2], item[3])  # the edit fills it
    assert world[named(world, win, 'view_navbar')].visible == 0  # the toolbar hides with the keyboard down
    assert world[named(world, win, 'img_return')].props['style'] == 'default'
    for name in LISTS:  # Classic's own geometry's search rows
        assert world[world[named(world, win, name)].parent].props['default_item_height'] == GEOMETRY['search_row']
    tab_px = {v for k, v in world[named(world, win, TABS[0])].props.items() if k.endswith('font_size')}
    for name in MESSAGES:  # a note, under the tabs' size
        px_ = {v for k, v in world[named(world, win, name)].props.items() if k.endswith('font_size')}
        assert px_ and max(px_) < min(tab_px)
    assert st('search_win', C.c_void_p).value == win
    assert st('search_tabs', C.c_void_p).value == named(world, win, 'tab_button_group')
    for gone in world.on(win, EVT_DESTROY):
        gone()
    assert st('search_win', C.c_void_p).value is None and st('search_tabs', C.c_void_p).value is None


@pytest.mark.parametrize('missing', ['tab_control', 'tab_button_group', 'pages'])
def test_a_search_dialog_missing_its_tabs_is_left(fw, world, st, missing):
    theme(fw, CLASSIC)
    win = search_window(world, missing=(missing,))
    ui_load(fw, st, b'search_dialog', win)
    assert fw.calls('widget_move_resize') == [] and st('search_win', C.c_void_p).value is None


def test_search_s_tabs_take_the_bin_s_words(fw, world, st):
    theme(fw, CLASSIC)
    win = search_window(world)
    ui_load(fw, st, b'search_dialog', win)
    fw.mock('stock_search_init_trampoline', lambda *_: 9)
    init = fw.fn('ringnav_search_init', C.c_int, C.c_void_p, C.c_void_p)
    assert init(win, 0x77) == 9
    assert [world[named(world, win, n)].text for n in TABS] == ['Songs', 'Artists', 'Albums', 'Genres']
    other = world.window('search_dialog', 'dialog')
    assert init(other, 0x77) == 9 and len(fw.calls('widget_set_text')) == 4  # another window's tabs: stock's words


def search_over(fw, win):
    over = Canvas(fw)
    fw.fn('search_over', None, C.c_void_p, C.c_void_p)(win, over.p)
    return over.fills


@pytest.mark.parametrize('results', [True, False])
def test_search_s_dark_strip_ends_under_the_tabs_or_under_the_field(fw, world, st, results):
    """From under the bar, round the field; with results it holds the tabs and ends where their pages start, with
    the history it ends as far under the field as it starts above (SEARCH_HISTORY_Y's 7 rows either side)."""
    win, bar, canvas = search_paint(fw, world, st, results)
    rows = sorted(canvas.rows(x=0, w=375))
    top, end = rows[0], rows[-1] + 1
    x, y, w, h = field_image(canvas)
    assert rows == list(range(top, end)) and top == world.at(bar)[3] and top < y and y + h < end
    if results:
        assert end == world.at(named(world, win, 'tab_control'))[1] + world.at(named(world, win, 'pages'))[1]
    else:
        assert end - (y + h) == y - top
    assert ('widget_paint', bar) in canvas.arts and canvas.fill == 0xff123456
    (fx, fy, fw_, fh, c), = search_over(fw, win)  # the strip's row below it, over the rows
    assert (fx, fy, fw_, fh) == (0, end, world.at(win)[2], 1) and c & 0xffffff == 0 and 0 < c >> 24 < 255  # a shade


def test_with_the_keyboard_up_search_s_strip_is_the_top_of_the_screen(fw, world, st):
    """Down to the keyboard, round the field, which moves over the keyboard; the chevron over Return's tap."""
    win, bar, canvas = search_paint(fw, world, st, keyboard=True)
    kb = st('kb_win', C.c_void_p).value
    rows = sorted(canvas.rows(x=0, w=375))
    assert rows == list(range(world.at(kb)[1]))
    x, y, w, h = field_image(canvas)
    item = world.at(named(world, win, 'list_item_search'))
    assert item[:3] == (x, y, w) and h == item[3] + 1 and y + h <= len(rows)
    assert world.at(named(world, win, 'search_edit')) == (0, 0, item[2], item[3])
    assert world[named(world, win, 'view_navbar')].visible  # the toolbar back with the keyboard
    (back,) = canvas.art('classic_light_kb_back')
    assert inside(back, world.at(named(world, win, 'img_return'))) and back[0] + back[2] <= x
    assert ('widget_paint', bar) not in canvas.arts  # no bar over the keyboard's page
    assert search_over(fw, win) == []


def test_search_s_field_shows_its_hint_smaller_than_a_query(fw, world, st):
    """The hint smaller still with the keyboard up, where the field is narrower; a query's size either way.
    Written once per change: it's on the paint."""
    win, _, _ = search_paint(fw, world, st)
    edit = named(world, win, 'search_edit')
    px_ = {}
    for typed, keyboard in [('', False), ('abba', False), ('', True), ('abba', True)]:
        if keyboard and not st('kb_win', C.c_void_p).value:
            keyboard_up(fw, world, st)
        world.set_text(edit, typed)
        paint_bg(fw, win, Canvas(fw))
        px_[typed, keyboard] = world[edit].props['style:disable:font_size']
        sets = len(fw.calls('widget_set_prop_int'))
        paint_bg(fw, win, Canvas(fw))
        assert len(fw.calls('widget_set_prop_int')) == sets
    assert px_['', True] < px_['', False] < px_['abba', False] == px_['abba', True]


def history_row(world, win, style):
    row = world.add(named(world, win, 'list_view'), 'row', 'list_item', box=(0, 0, 375, 52))
    return row, world.add(row, 'btn', 'button', box=(320, 0, 40, 52), style=style)


@pytest.mark.parametrize('selected', [0, 1])
def test_a_history_row_s_x_is_the_theme_s_mark(fw, world, st, scheme, selected):
    theme(fw, CLASSIC, LIGHT='1')  # where the selection's text and the ink differ
    win = search_window(world)
    ui_load(fw, st, b'search_dialog', win)
    row, x = history_row(world, win, 's_btn_cancel_navbar')
    st('sel_accent', C.c_void_p).value = row if selected else None
    canvas = Canvas(fw)
    paint_bg(fw, x, canvas, stock=5)
    assert fw.calls('stock_paint_bg_trampoline') == []  # stock's image and border give way
    runs = [(fx, fy, w) for fx, fy, w, h, _ in canvas.fills]
    rows = sorted({y for _, y, _ in runs})
    _, _, bw, bh = world.at(x)
    # search_x's comment: 3 px runs, 15 x 13, two strokes crossing on the middle row
    assert {w for *_, w in runs} == {3} and rows == list(range(rows[0], rows[0] + 13)) and len(runs) == 2 * 13 - 1
    left, right = min(fx for fx, _, _ in runs), max(fx + 3 for fx, _, _ in runs)
    assert right - left == 15 and left + right - 1 == bw  # centred in the button
    assert rows[0] + rows[-1] == bh
    assert {c for *_, c in canvas.fills} == {rgba(scheme('sel_text') if selected else scheme('ink'))}
    assert scheme('sel_text') != scheme('ink')
    assert canvas.fill == 0xff123456


def test_a_history_header_s_trash_is_stock_s(fw, world, st):
    theme(fw, CLASSIC)
    win = search_window(world)
    ui_load(fw, st, b'search_dialog', win)
    _, trash = history_row(world, win, 's_btn_delete_navbar')
    canvas = Canvas(fw)
    assert paint_bg(fw, trash, canvas, stock=5) == 5 and canvas.fills == []


def test_an_x_outside_search_is_stock_s(fw, world, st):
    theme(fw, CLASSIC)
    row = world.add(world.window('playlist_page'), 'row', 'list_item')
    x = world.add(row, 'btn', 'button', box=(320, 0, 40, 52), style='s_btn_cancel_navbar')
    assert paint_bg(fw, x, Canvas(fw), stock=5) == 5


def tabs(fw, world, st, light='0'):
    theme(fw, CLASSIC, LIGHT=light)
    win = search_window(world)
    ui_load(fw, st, b'search_dialog', win)
    return win, named(world, win, 'tab_button_group')


@pytest.mark.parametrize('lit', [0, 2, 3])
@pytest.mark.parametrize('light', ['0', '1'])
def test_the_lit_tab_fills_its_own_slot(fw, world, st, lit, light):
    win, group = tabs(fw, world, st, light)
    plain = Canvas(fw)
    paint_bg(fw, group, plain)
    world.set(named(world, win, TABS[lit]), 'value', 1)
    canvas = Canvas(fw)
    paint_bg(fw, group, canvas)
    extra = added(canvas, plain)  # all the plain track's, and more
    x, _, w, _ = world.at(named(world, win, TABS[lit]))
    assert extra and all(x <= fx and fx + fw_ <= x + w for fx, _, fw_, _, _ in extra)
    assert {fy for _, fy, *_ in extra} == set(range(world.at(group)[3] + 1))  # the track's rows and the light row under it


@pytest.mark.parametrize('light', ['0', '1'])
def test_the_tab_track_takes_the_half_s_ground(fw, world, st, scheme, light):
    """Nearer the half's paper than its ink: a light track in the light half, a dark one in the dark."""
    win, group = tabs(fw, world, st, light)
    canvas = Canvas(fw)
    paint_bg(fw, group, canvas)
    _, _, _, h = world.at(group)
    middle = max((f for f in canvas.fills if f[1] == h // 2), key=lambda f: f[2])  # its middle row, across it
    tone, paper, ink = luma(rgb_of(middle[4])), luma(scheme('paper')), luma(scheme('ink'))
    assert abs(tone - paper) < abs(tone - ink)


def test_each_tab_s_label_has_a_shadow_a_pixel_above_it(fw, world, st):
    win, group = tabs(fw, world, st)
    canvas = Canvas(fw)
    paint_bg(fw, group, canvas)
    boxes = [world.at(named(world, win, n)) for n in TABS]
    assert [(t, r) for t, r, *_ in canvas.texts] == [(world[named(world, win, n)].text, (x, y - 1, w, h))
                                                     for n, (x, y, w, h) in zip(TABS, boxes)]
    assert all(c & 0xffffff == 0 and 0 < c >> 24 < 255 for _, _, c, _ in canvas.texts)  # black, faint
    assert canvas.fill == 0xff123456


# Classic's New Playlist and Rename: the keyboard's page under the bar.

def playlist_box(world, name='addplaylist_dialog', item='list_item_ssedit', edit='ssedit', back=True):
    win = world.window(name, 'dialog')
    row = world.add(win, item, 'list_item', box=(0, 60, 375, 50), bg_color=0x12345678)
    world.add(row, edit, 'edit', box=(10, 5, 355, 40))
    if back:
        world.add(win, 'img_return', 'image', box=(0, 0, 60, 60))
    return win


@pytest.mark.parametrize('name, item, edit', [('addplaylist_dialog', 'list_item_ssedit', 'ssedit'),
                                              ('renameplaylist_dialog', 'list_item_edit', 'edit')])
def test_the_playlist_box_s_field_sits_beside_the_chevron(fw, world, st, name, item, edit):
    theme(fw, CLASSIC)
    win = playlist_box(world, name, item, edit)
    ui_load(fw, st, name.encode(), win)
    field, box, back = world.at(named(world, win, item)), world.at(named(world, win, edit)), world.at(named(world, win, 'img_return'))
    assert back[0] + back[2] == field[0]  # the chevron's tap right up to the field
    assert box == (0, 0, field[2], field[3])  # the edit fills it
    assert abs((back[1] + back[3] / 2) - (field[1] + field[3] / 2)) <= 1  # on one line
    props = world[named(world, win, item)].props
    assert len([k for k in props if k.startswith('style:')]) == 15 and {props[k] for k in props if k.startswith('style:')} == {0}
    assert world[named(world, win, edit)].props['style:normal:margin_left'] == 15  # no glass to clear
    assert st('pl_box', C.c_void_p).value == win and [ms for _, _, ms in world.timers] == [0]
    for gone in world.on(win, EVT_DESTROY):
        gone()
    assert st('pl_box', C.c_void_p).value is None


def test_a_playlist_box_without_its_return_is_left(fw, world, st):
    theme(fw, CLASSIC)
    win = playlist_box(world, back=False)
    ui_load(fw, st, b'addplaylist_dialog', win)
    assert fw.calls('widget_move_resize') == [] and st('pl_box', C.c_void_p).value is None


@pytest.mark.parametrize('with_bar', [0, 1])
def test_the_playlist_box_s_page_darkens_down_to_the_keyboard(fw, world, st, with_bar):
    theme(fw, CLASSIC)
    bar = system_bar(fw, world) if with_bar else 0
    win = playlist_box(world)
    ui_load(fw, st, b'addplaylist_dialog', win)
    canvas = Canvas(fw)
    fw.mock('widget_paint', lambda w, c, *_: canvas.arts.append(('widget_paint', w)))
    paint_bg(fw, win, canvas)
    rows = canvas.rows(x=0, w=375)
    assert sorted(rows) == list(range(320))
    shades = [luma(rgb_of(rows[y])) for y in range(320)]
    assert shades == sorted(shades, reverse=True) and shades[0] > shades[-1]
    assert canvas.art('classic_light_kb_back') and canvas.art('classic_light_field')
    assert canvas.art('classic_light_field_glass') == []  # a name, not a query: no glass
    assert (('widget_paint', bar) in canvas.arts) == bool(bar) and canvas.fill == 0xff123456


# Classic's slider pages.

SLIDER_LIGHT, SLIDER_MAX, SLIDER_BALANCE, SLIDER_BOOT = range(4)
SLIDER_PAGES = ['backlight_page', 'maxvol_page', 'balance_page', 'bootvol_page']
SLIDERS = ['slider_backlight', 'slider_maxvol', 'slider_balance', 'slider_bootvol']


def slider_page(fw, world, st, kind, value=50, band=True, number=None):
    """Stock's slider page of kind as Classic loads it (slider_open), its band's view shown or not: (window,
    slider)."""
    theme(fw, CLASSIC)
    win = world.window(SLIDER_PAGES[kind])
    view = world.add(win, 'view_slider', box=(0, 120, 375, 60))
    slider = world.add(view, SLIDERS[kind], 'slider', box=(81, 15, 204, 30), min=0, max=100, value=value)
    if number is not None:
        world.add(win, 'label_balance', 'label', text=number)
    ui_load(fw, st, SLIDER_PAGES[kind].encode(), win)
    world[view].visible = band
    return win, slider


def slider_paint(fw, win):
    canvas = Canvas(fw)
    paint_bg(fw, win, canvas)
    return canvas


def on_page(world, w):
    """w's box in its window's coordinates."""
    x, y, ww, h = world.at(w)
    px_, py, *_ = world.at(world[w].parent)
    return x + px_, y + py, ww, h


@pytest.mark.parametrize('value', [0, 25, 100])
def test_max_volume_s_triangle_points_at_the_fill_s_end(fw, world, st, value):
    """Tip up, a row wider each row down, centred on the fill's end, inside the band."""
    win, slider = slider_page(fw, world, st, SLIDER_MAX, value)
    canvas = slider_paint(fw, win)
    tip = sorted((y, x, w) for x, y, w, h, c in canvas.fills if h == 1 and c == 0xffffffff)
    ys, widths = [y for y, _, _ in tip], [w for *_, w in tip]
    assert len(ys) > 1 and ys == list(range(ys[0], ys[0] + len(ys)))  # rows one under another
    assert widths == sorted(widths) and widths[0] < widths[-1]
    x, _, w, _ = on_page(world, slider)
    assert all(fx + fw_ / 2 == x + w * value // 100 for _, fx, fw_ in tip)
    (top,), (foot,) = canvas.art('classic_light_band_top'), canvas.art('classic_light_band_foot')
    assert top[1] <= ys[0] and ys[-1] < foot[1]


@pytest.mark.parametrize('kind, ends', [(SLIDER_MAX, ('spk_quiet_w', 'spk_loud_w')), (SLIDER_BOOT, ('spk_quiet_w', 'spk_loud_w')),
                                        (SLIDER_LIGHT, ('sun_small', 'sun_big')), (SLIDER_BALANCE, ())])
def test_each_slider_page_s_band_has_its_ends(fw, world, st, kind, ends):
    win, slider = slider_page(fw, world, st, kind)
    canvas = slider_paint(fw, win)
    assert canvas.art('classic_light_band_top') and canvas.art('classic_light_band_foot')
    drawn = [n for n, _ in canvas.arts if n and n.startswith(('classic_light_spk', 'classic_light_sun'))]
    assert drawn == ['classic_light_' + e for e in ends]
    if ends:
        x, _, w, _ = on_page(world, slider)
        (low,), (high,) = canvas.art('classic_light_' + ends[0]), canvas.art('classic_light_' + ends[1])
        assert low[0] < x and high[0] >= x + w - 1  # outside the slider, either end


def test_a_slider_page_s_caption_has_its_shadow_a_pixel_under_it(fw, world, st):
    win, _ = slider_page(fw, world, st, SLIDER_MAX)
    canvas = slider_paint(fw, win)
    caption = named(world, win, 'caption')
    x, y, w, h = world.at(caption)
    props = world[caption].props
    (text, r, color, font), = canvas.texts
    assert text == world[caption].text and r == (x, y + 1, w, h)
    assert font == (props['style:normal:font_name'], props['style:normal:font_size'])  # in its face and size
    assert color & 0xffffff == 0 and 0 < color >> 24 < 255  # black, faint


def test_a_hidden_band_draws_no_band(fw, world, st):
    win, _ = slider_page(fw, world, st, SLIDER_MAX, band=False)
    canvas = slider_paint(fw, win)
    assert canvas.art('classic_light_band_top') == [] and not [f for f in canvas.fills if f[4] == 0xffffffff]
    assert canvas.texts  # the caption still


def test_the_value_s_label_takes_the_page_s_number_box_once(fw, world, st):
    """Across the page, over the band; stock writes its text after the load, and its face is checked again."""
    win, _ = slider_page(fw, world, st, SLIDER_BALANCE, number='')
    label = named(world, win, 'label_balance')
    world.set_text(label, 'L 3')
    canvas = slider_paint(fw, win)
    x, y, w, h = world.at(label)
    (top,) = canvas.art('classic_light_band_top')
    assert (x, w) == (0, world.at(win)[2]) and y + h <= top[1] and world[label].visible
    moves = len(fw.calls('widget_move_resize'))
    slider_paint(fw, win)
    assert len(fw.calls('widget_move_resize')) == moves


def test_boot_volume_finds_its_switch(fw, world, st):
    win, _ = slider_page(fw, world, st, SLIDER_BOOT)
    switch = world.add(win, 'img_bootswitch', 'image', image='switch_on')
    canvas = slider_paint(fw, win)
    assert st('slider_sw', C.c_void_p).value == switch
    assert canvas.art('classic_light_tray_shadow')  # its tray drawn, as the switch is shown
    world[switch].visible = 0
    assert slider_paint(fw, win).art('classic_light_tray_shadow') == []


def slider_scrub(fw, slider):
    canvas = Canvas(fw)
    fw.fn('slider_scrub', None, C.c_void_p, C.c_void_p)(slider, canvas.p)
    return canvas


def bar_rows(width):
    """The slider's bar, w wide: its rows 31 to 38 of the slider's touch (slider_open's comment), the first and
    last inset a pixel at each end (slider_scrub's)."""
    return [(1, 31, width - 2)] + [(0, y, width) for y in range(32, 38)] + [(1, 38, width - 2)]


@pytest.mark.parametrize('value', [50, 80])
def test_the_slider_s_bar_is_a_track_and_its_lit_part(fw, world, st, value):
    _, slider = slider_page(fw, world, st, SLIDER_LIGHT, value)
    canvas = slider_scrub(fw, slider)
    w = world.at(slider)[2]
    part = w * value // 100
    track = [f for f in canvas.fills if f[2] in (w, w - 2)]
    lit = [f for f in canvas.fills if f[2] in (part, part - 2)]
    assert [(x, y, fw_) for x, y, fw_, *_ in track] == bar_rows(w)
    assert [(x, y, fw_) for x, y, fw_, *_ in lit] == bar_rows(part)  # the value's share, from the start
    assert all(luma(rgb_of(l[4])) > luma(rgb_of(t[4])) for l, t in zip(lit, track))  # lit over the dark track
    assert canvas.fill == 0xff123456


def test_an_almost_empty_slider_has_no_lit_part(fw, world, st):
    _, slider = slider_page(fw, world, st, SLIDER_LIGHT, 1)
    assert len(slider_scrub(fw, slider).fills) == 8


def test_another_slider_is_stock_s(fw, world, st):
    _, slider = slider_page(fw, world, st, SLIDER_LIGHT)
    other = world.add(world.top, 'slider2', 'slider', box=(0, 0, 100, 30))
    assert slider_scrub(fw, other).fills == []


# Classic's message screens: Update Library's dialog and USB mode's page.

SCAN_WHOLE = O['SCAN_WHOLE']


@pytest.fixture
def whole(fw):
    """Stock's whole-library scan flag, at its fixed address: conftest's map outlives the test, so it goes back."""
    flag = C.c_ubyte.from_address(SCAN_WHOLE)
    saved = flag.value
    yield flag
    flag.value = saved


def scan_dialog(fw, world, st, text='Scanned 0', create=0):
    theme(fw, CLASSIC)
    win = world.window('updatemusic_dialog', 'dialog')
    for name in ('img_animation', 'label_scanfolder'):
        world.add(win, name, 'image')
    world.add(win, 'label_msg', 'label', text=text)
    world.add(win, 'btn_no', 'button', box=(100, 250, 175, 40), text='Stop')
    threads = []
    fw.mock('fw_pthread_create', lambda t, attr, fn, arg, *_: threads.append(fn) or create)
    ui_load(fw, st, b'updatemusic_dialog', win)
    return win, threads


def count(fw, threads, songs):
    """Run the dialog's counting thread over a library of songs."""
    def exec_(db, sql, row, ctx, *_):
        if row and b'count(*)' in C.string_at(sql):
            values = (C.c_char_p * 1)(str(songs).encode())
            C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.c_void_p, C.c_void_p)(row)(ctx, 1, C.addressof(values), 0)
        return 0
    fw.mock('sqlite3_exec', exec_)
    (thread,) = threads
    C.CFUNCTYPE(C.c_void_p, C.c_void_p)(thread)(None)


def test_the_scan_dialog_is_classic_s_message_screen(fw, world, st):
    win, threads = scan_dialog(fw, world, st)
    for name in ('img_animation', 'label_msg', 'label_scanfolder'):
        assert world[named(world, win, name)].visible == 0  # stock's widgets stay for its look-ups, hidden
    heading, line = named(world, win, 'msg_heading'), named(world, win, 'msg_line')
    assert world[heading].text and world.at(heading)[3] > world.at(line)[3]  # a heading, taller than the line
    x, y, w, h = world.at(named(world, win, 'btn_no'))
    assert 2 * x + w == world.at(win)[2]  # centred
    assert world[named(world, win, 'btn_no')].props['style:normal:font_name'] == 'classic-light'
    assert len(threads) == 1 and fw.calls('fw_pthread_detach')
    (fn, _, ms), = world.timers
    assert ms == 200 and st('msg_win', C.c_void_p).value == win
    for gone in world.on(win, EVT_DESTROY):
        gone()
    assert st('msg_win', C.c_void_p).value is None and world.removed == [1]


def test_one_count_answers_two_dialogs(fw, world, st):
    _, threads = scan_dialog(fw, world, st)
    win2 = world.window('updatemusic_dialog', 'dialog')
    world.add(win2, 'label_msg', 'label', text='0')
    world.add(win2, 'btn_no', 'button')
    ui_load(fw, st, b'updatemusic_dialog', win2)
    assert len(threads) == 1  # still running


def test_a_count_that_can_t_start_lets_the_next_dialog_try(fw, world, st):
    _, threads = scan_dialog(fw, world, st, create=11)
    assert len(threads) == 1 and fw.calls('fw_pthread_detach') == []
    win2 = world.window('updatemusic_dialog', 'dialog')
    world.add(win2, 'label_msg', 'label', text='0')
    world.add(win2, 'btn_no', 'button')
    ui_load(fw, st, b'updatemusic_dialog', win2)
    assert len(threads) == 2  # tried again


def poll(world):
    return world.fire(1)


def msg_paint(fw, win, y=0, world=None):
    if world:
        world.box(win, 0, y, 375, 320 - y)
    canvas = Canvas(fw)
    paint_bg(fw, win, canvas)
    return canvas


def scan_bar(fills):
    """The scan's bar among fills, if it has its track (the translucent black rows): the track's x, end and rows,
    and its fill's end (the opaque rows', the furthest) and rows."""
    track = [f for f in fills if not f[4] & 0xffffff and 0 < f[4] >> 24 < 255]
    if not track:
        return None
    lit = [f for f in fills if f[4] >> 24 == 255]
    return (min(f[0] for f in track), max(f[0] + f[2] for f in track), {f[1] for f in track},
            max((f[0] + f[2] for f in lit), default=-1), {f[1] for f in lit})


def counted(fw, world, st, whole, text, songs=704):
    """A whole-library scan's dialog at stock's text, painted before the count answers and after: the dialog and
    what the count added (scan_bar's)."""
    whole.value = 1
    win, threads = scan_dialog(fw, world, st, text)
    before = msg_paint(fw, win)
    count(fw, threads, songs)
    return win, scan_bar(added(msg_paint(fw, win), before))


def test_the_scan_counts_against_the_library_before_it(fw, world, st, whole):
    whole.value = 1
    win, threads = scan_dialog(fw, world, st, 'Scanned 312')
    before = msg_paint(fw, win)
    count(fw, threads, 704)
    assert poll(world) == RET_REPEAT
    line, button = named(world, win, 'msg_line'), named(world, win, 'btn_no')
    assert world[line].text == '312 of 704 songs'  # msg_sync's own example
    assert world[button].text not in ('Stop', '', None)  # over stock's word
    (target, box), = world.invalid
    after = msg_paint(fw, win)  # the line in its place, the bar drawn
    bar = added(after, before)
    assert target == win and scan_bar(bar) and inside(world.at(line), box)
    assert all(inside(f[:4], box) for f in bar)  # the line and the bar repainted
    assert poll(world) == RET_REPEAT and len(world.invalid) == 1  # nothing new: nothing repainted


@pytest.mark.parametrize('text, held', [('704', True), ('Scanned 700 of the', True), ('Scanned 600', False)])
def test_the_bar_holds_under_its_end_until_the_dialog_closes(fw, world, st, whole, text, held):
    """A count at or near the total holds the fill at 99% of the track (docs/internals.md), so the bar never reads
    done while the dialog is up; under that it is the count's share."""
    _, (x, end, _, fill_end, _) = counted(fw, world, st, whole, text)
    share = x + (end - x) * int(re.findall(r'\d+', text)[0]) // 704
    assert fill_end < end and fill_end == (x + (end - x) * 99 // 100 if held else share) and fill_end <= share


@pytest.mark.parametrize('flag, text, songs', [(0, 'Scanned 312', 704), (1, 'Scanned 800', 704), (1, 'Scanning', 704),
                                               (1, 'Scanned 3', 0)])
def test_without_a_total_to_measure_the_line_is_stock_s_and_no_bar(fw, world, st, whole, flag, text, songs):
    """A folder's scan, a scan past the total, no number yet, or no library before it."""
    whole.value = flag
    win, threads = scan_dialog(fw, world, st, text)
    before = msg_paint(fw, win)
    count(fw, threads, songs)
    poll(world)
    assert world[named(world, win, 'msg_line')].text == text and added(msg_paint(fw, win), before) == []


def test_before_the_count_answers_the_line_is_stock_s_and_no_bar(fw, world, st, whole):
    whole.value = 1
    win, threads = scan_dialog(fw, world, st, 'Scanned 9')
    poll(world)
    before = msg_paint(fw, win)
    assert world[named(world, win, 'msg_line')].text == 'Scanned 9'
    count(fw, threads, 704)
    assert scan_bar(added(msg_paint(fw, win), before))  # the track comes with the count


def test_the_scan_s_bar_fills_to_its_part(fw, world, st, whole):
    win, (x, end, rows, fill_end, fill_rows) = counted(fw, world, st, whole, 'Scanned 352')
    assert fill_end == x + (end - x) * 352 // 704 and fill_rows <= rows  # the count's share, on the track
    heading, line = named(world, win, 'msg_heading'), named(world, win, 'msg_line')
    assert world.at(heading)[1] < world.at(line)[1] and world.at(line)[1] + world.at(line)[3] <= min(rows)
    assert {'352', '704'} <= set(re.findall(r'\d+', world[line].text))  # the line carries both numbers
    canvas = msg_paint(fw, win)
    texts = [t for t, *_ in canvas.texts]
    assert texts[1:] == [world[heading].text, world[line].text]  # under the dark bar's title, their shadows
    assert canvas.art('classic_light_orb_note') and canvas.fill == 0xff123456


def test_a_message_window_under_the_bar_draws_no_bar_of_its_own(fw, world, st, whole):
    """The same page lower down: its own page from its top, the rest where it was on the screen, no title."""
    win, _ = scan_dialog(fw, world, st, 'Scanned 9')
    full = msg_paint(fw, win)
    canvas = msg_paint(fw, win, 26, world)
    _, dy, w, h = world.at(win)
    assert [t for t, *_ in full.texts][1:] == [t for t, *_ in canvas.texts]  # all but the dark bar's title
    assert canvas.fills[0][:4] == (0, 0, w, h)  # the page from its own top
    (orb,), (was,) = canvas.art('classic_light_orb_note'), full.art('classic_light_orb_note')
    assert orb[1] + dy == was[1]  # the screen's place, in the window's rows


USB_LUN = '/sys/kernel/config/usb_gadget/storage_demo/functions/mass_storage.msg/lun.0/file'


def usb_page(fw, world, st, number=CLASSIC, mode=0):
    theme(fw, number)
    fw.var('g_usbmode', C.c_ubyte).value = mode
    win = world.window('usbmode_page')
    world.add(win, 'img_usbmode', 'image')
    world.add(win, 'scrlabel_info', 'hscroll_label', box=(20, 200, 335, 30), text='USB Connected')
    ui_load(fw, st, b'usbmode_page', win)
    return win


def test_classic_s_usb_page_says_connected_and_to_eject(fw, world, st):
    """A heading and a line, under the dark bar titled as the bar titles the page; no bar, no Cancel."""
    win = usb_page(fw, world, st)
    heading, line = world[named(world, win, 'msg_heading')].text, world[named(world, win, 'msg_line')].text
    assert heading and line and heading != line
    assert world[named(world, win, 'scrlabel_info')].visible == 0 and st('usb_win', C.c_void_p).value == win
    title = bar_title(fw, world, st)
    sync(fw, win)
    canvas = msg_paint(fw, win)
    assert [t for t, *_ in canvas.texts] == [world[title].text, heading, line]
    below = world.at(named(world, win, 'msg_line'))[1]
    assert [f for f in canvas.fills if f[1] >= below] == [] and canvas.art('classic_light_orb_usb')
    for gone in world.on(win, EVT_DESTROY):
        gone()
    assert st('usb_win', C.c_void_p).value is None


def test_usb_audio_mode_has_nothing_to_eject(fw, world, st):
    win = usb_page(fw, world, st, mode=1)
    assert world[named(world, win, 'msg_line')].text is None and world.timers == []


@pytest.mark.parametrize('number', [CLASSIC, MINIMAL])
def test_the_computer_s_eject_says_ok_to_disconnect(fw, world, st, number):
    """Classic's heading says so and its line goes; elsewhere a label of internals.md's style covers stock's line,
    which stock's timer would write back."""
    files = Files(fw, {USB_LUN: b'/dev/mmcblk0p1\n'})
    win = usb_page(fw, world, st, number)
    (_, ctx, ms), = world.timers
    assert ctx == win and ms == 500
    info = named(world, win, 'scrlabel_info')
    was = world[named(world, win, 'msg_heading') or info].text
    assert poll(world) == RET_REPEAT and st('usb_lun').value == 1  # the LUN open: storage in use
    files.files[USB_LUN] = b''
    assert poll(world) == RET_REMOVE and world.invalid[-1] == (win, None)
    if number == CLASSIC:
        assert world[named(world, win, 'msg_heading')].text not in (was, '', None)
        assert world[named(world, win, 'msg_line')].text == ''
    else:
        (ok,) = [c for c in world[win].children if c != info and world[c].kind == 'hscroll_label']
        assert world[ok].text not in (was, '', None)
        assert world.at(ok) == world.at(info) and world[info].visible == 0
        assert world[ok].props['style'] == 's_scrlabel_white24c'


@pytest.mark.parametrize('files', [{}, {USB_LUN: b''}])
def test_no_lun_open_yet_is_no_eject(fw, world, st, files):
    """No file: the gadget isn't built; an empty one before it ever read open isn't an eject either."""
    Files(fw, dict(files))
    usb_page(fw, world, st)
    assert [poll(world) for _ in range(3)] == [RET_REPEAT] * 3 and st('usb_lun').value == 0


# Loading a window.

def test_a_loaded_window_is_stock_s_root(fw, world, st):
    theme(fw, MINIMAL)
    win = world.window('albums_page')
    assert ui_load(fw, st, b'albums_page', win) == (win, ['albums_page'])
    assert ui_load(fw, st, b'albums_page', 0)[0] == 0


@pytest.mark.parametrize('centred, page', [('0', 'playing_page'), ('1', 'playing_page_stock')])
def test_now_playing_loads_the_layout_centred_now_playing_picks(fw, world, st, centred, page):
    theme(fw, MINIMAL, NPCENTRED=centred)
    win = world.window('playing_page')
    _, asked = ui_load(fw, st, b'playing_page', win)
    assert asked[0].endswith(page)  # the set's own copy, if it has one, of that page


@pytest.mark.parametrize('name', ['showsn_dialog', 'checkfw_dialog'])
@pytest.mark.parametrize('number, cleared', [(CLASSIC, 1), (MINIMAL, 0)])
def test_the_small_dialogs_don_t_dim_what_is_under_them(fw, world, st, name, number, cleared):
    theme(fw, number)
    win = world.window(name, 'dialog', highlight='default(alpha=200)')
    ui_load(fw, st, name.encode(), win)
    assert (world[win].props['highlight'] == '') == bool(cleared)


def test_a_dialog_s_highlight_is_stock_s_for_the_others(fw, world, st):
    theme(fw, CLASSIC)
    win = world.window('msginfo_dialog', 'dialog', highlight='default(alpha=200)')
    ui_load(fw, st, b'msginfo_dialog', win)
    assert world[win].props['highlight'] == 'default(alpha=200)'


# The full-screen art (bigcover_page_init).

def test_the_full_screen_art_shows_its_name_then_fades_it(fw, world, st):
    """The name and its shade shown BC_MS, then faded together, BC_STEPS steps BC_STEP_MS apart, to nothing."""
    theme(fw, MINIMAL)
    fw.mock('stock_bigcover_trampoline', lambda *_: 4)
    win = world.window('bigcover_page')
    title, shade = world.add(win, 'scrlabel_title', 'hscroll_label'), world.add(win, 'view_shade')
    assert fw.fn('ringnav_bigcover', C.c_int, C.c_void_p, C.c_void_p)(win, 0x55) == 4
    assert (st('bc', C.c_void_p * 3)[:]) == [title, shade, win]

    def shown():
        seen = {}
        for _, a in fw.calls('widget_set_opacity'):
            seen.setdefault(a[0], []).append(a[1])
        return seen
    assert shown() == {title: [255], shade: [255]}
    (_, _, ms), = world.timers
    assert ms == O['BC_MS'] and world.fire(1) == RET_OK  # the wait ends, the steps begin
    (_, _, step), = world.timers[1:]
    while world.fire(2) == RET_REPEAT:
        pass
    steps = shown()[title]
    assert step == O['BC_STEP_MS'] and shown()[shade] == steps and len(steps) == 1 + O['BC_STEPS']
    assert steps == sorted(steps, reverse=True) and len(set(steps)) == len(steps) and steps[-1] == 0
    for gone in world.on(win, EVT_DESTROY):
        gone()
    assert st('bc', C.c_void_p * 3)[:] == [None, None, None]


def test_closing_the_full_screen_art_drops_its_fade(fw, world, st):
    theme(fw, MINIMAL)
    fw.mock('stock_bigcover_trampoline', lambda *_: 4)
    win = world.window('bigcover_page')
    world.add(win, 'scrlabel_title', 'hscroll_label'), world.add(win, 'view_shade')
    fw.fn('ringnav_bigcover', C.c_int, C.c_void_p, C.c_void_p)(win, 0x55)
    for gone in world.on(win, EVT_DESTROY):
        gone()
    assert world.removed == [1] and st('bc', C.c_void_p * 3)[:] == [None, None, None]


def test_no_full_screen_art_window_is_stock_s_answer(fw, world):
    theme(fw, MINIMAL)
    fw.mock('stock_bigcover_trampoline', lambda *_: 3)
    assert fw.fn('ringnav_bigcover', C.c_int, C.c_void_p, C.c_void_p)(None, 0x55) == 3
    assert world.handlers == [] and world.timers == []


def test_a_set_date_page_is_painted_as_the_time_form(fw, world, st):
    """The slider window with no slider is Set Date's or Set Time's: its own painter, its Save under it."""
    theme(fw, CLASSIC)
    win = world.window('manualtime_page')
    st('slider_win', C.c_void_p).value = win
    canvas = Canvas(fw)
    paint_bg(fw, win, canvas)
    assert 'Save' in [t for t, *_ in canvas.texts] and canvas.art('classic_light_band_top') == []


# The theme's boot, at the system bar's load (application_init's first window).

def boot(fw, world, st):
    bar = world.window('system_bar', 'system_bar', top=False)
    fw.mock('stock_ui_load_trampoline', lambda *_: bar)
    load = fw.fn('ringnav_ui_load', C.c_void_p, C.c_char_p, C.c_void_p)
    assert load(b'system_bar', 0) == bar
    return load


def test_a_set_the_boot_can_t_write_boots_as_minimal(fw, world, st):
    theme(fw, CLASSIC)
    fw.mock('theme_mprotect', lambda *_: -1)
    boot(fw, world, st)
    assert st('theme_booted').value == 1 and st('theme').value == 0 and st('geom').value == 0


@in_fork
def test_the_system_bar_s_load_boots_the_theme_s_set_once(fw, world, st):
    """In a child: the boot writes the set's values into stock's code pages, which outlive a test here."""
    theme(fw, CLASSIC)
    load = boot(fw, world, st)
    want = fw.fn('theme_geom', C.c_int, C.c_void_p)(fw.fn('theme', C.c_void_p)())
    assert st('theme').value == 1 and st('geom').value == want != 0
    calls = len(fw.calls('theme_mprotect'))
    assert calls and load(b'system_bar', 0)
    assert len(fw.calls('theme_mprotect')) == calls  # once


# Boot may paint Home before the screen is usable: once the list is reachable, it is repainted.

def test_the_first_reachable_list_is_repainted_once(fw, world, st):
    theme(fw, MINIMAL)
    bar = system_bar(fw, world)
    win = world.window('games_page')
    rows = world.add(win, 'list', 'scroll_view')
    C.c_ubyte.from_buffer(world[rows].block, O['VIEW_VERTICAL']).value = 1
    paint_bg(fw, bar, Canvas(fw))
    assert (rows, None) not in world.invalid  # the screen isn't usable yet
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    paint_bg(fw, bar, Canvas(fw))
    paint_bg(fw, bar, Canvas(fw))
    assert world.invalid.count((rows, None)) == 1
