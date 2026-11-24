"""patch/card_theme.c: a card theme's whole-file check and its three lines (accent, page colour, battery), the
card-to-flash copy at a mount, the failed-start guard, the accent's and the page's scheme row under the themes that take
them and the battery style in all four, the flip on a page, and the Display rows it sets."""
import ctypes as C
import hashlib
import random
import re

import pytest

import fuzz
import host
from conftest import O, Config, block, rgba
from test_coverflow import ART, bgra, decoded, tex_rig
from test_coverflow import bitmap as cover
from test_coverflow import rgba as cover_pixels
from test_nav_display_library import Ui, appearance, button_of, row_of, writes
from test_nav_loop import Card, Clock, Mounts, logo_poll, toasts
from test_nav_paint import WHITE, Canvas, bitmaps, channels, image_add, paint_bg, px, rgb_of, strings, style_color, system_bar, world  # noqa: F401 (bitmaps, world: fixtures)

SRC, DST = '/mnt/mmc/Q2Pod/theme.txt', '/mnt/data/q2pod-theme'
BAD, TRY, SEEN = DST + '.bad', DST + '.try', DST + '.seen'
THEMES = host.tools_theme.builtins()
INDEX = {t['id']: t['index'] for t in THEMES}
STOCK_RED, CRIMSON, IVORY = O['STOCK_RED'], O['CRIMSON'], O['MINIMAL_ACCENT']
STYLES = ('icon', 'percent', 'both', 'inside')


def theme_file(*lines, head=b'q2theme 1', end=b'end', nl=b'\n'):
    return nl.join([head, *lines, end]) + nl


AUBERGINE = theme_file(b'name Aubergine', b'author Q2 Pod', b'accent #c04a8a', b'color bg #1e1226', b'battery inside')


@pytest.fixture(scope='module')
def ct_fields(library):
    return host.offsets(library.parent/'card_theme.o', 'ct')


@pytest.fixture
def ct(fw, ct_fields):
    base = C.addressof(fw.var('card_theme__ct', C.c_char))
    return lambda name, ctype=C.c_uint: ctype.from_address(base + ct_fields[name])


def parse(fw, data):
    return fw.fn('card_theme__card_theme_parse', C.c_int, C.c_void_p, C.c_uint)(block(fw, data), len(data))


def mix(a, b, j, n):
    return sum(((a >> s & 255) * (n - j) + (b >> s & 255) * j) // n << s for s in (0, 8, 16))


def luminance(c):
    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c >> 16) + 0.7152 * lin(c >> 8 & 255) + 0.0722 * lin(c & 255)


def on_white(c):
    """WCAG's contrast ratio of white text on c."""
    return 1.05 / (luminance(c) + 0.05)


def tones(c):
    """Q2 Theme Studio's tones of an accent c: top, bottom, light, red, highlight."""
    light = mix(c, 0xffffff, 12, 100)
    neutral = max(light >> s & 255 for s in (0, 8, 16)) - min(light >> s & 255 for s in (0, 8, 16)) <= O['LIGHT_SPREAD']
    return [c, c, light, 0xd8d8d8 if neutral else light, light]


# The whole-file check and the parse
@pytest.mark.parametrize('data', [
    b'',
    b'q2theme 2\naccent #c04a8a\nend\n',  # a newer format
    b'q2theme 1 \naccent #c04a8a\nend\n',  # not the head exactly
    b'Q2theme 1\naccent #c04a8a\nend\n',
    b'q2theme 1\naccent #c04a8a\n',  # cut off before its end
    b'q2theme 1\naccent #c04a8a\nend\nbattery both\n',  # end not last
    b'q2theme 1\naccent #c04a8a\nen',
    b'q2theme 1\naccent #c04a8a\0\nend\n',  # an embedded NUL
    b'\xef\xbb\xbf\xef\xbb\xbfq2theme 1\nend\n',  # one BOM only
    b'q2theme 1end\n',
    AUBERGINE + b'\n \t\n',  # a line of spaces after end isn't an empty one
    AUBERGINE[:-1] + b' \n',
    AUBERGINE[:-1] + b'\r\r\n',
])
def test_a_file_not_whole_is_refused_whole(fw, ct, data):
    assert parse(fw, data) == 0 and ct('accent').value == ct('bg').value == 0 and ct('battery', C.c_int).value == 0


@pytest.mark.parametrize('data', [
    AUBERGINE,
    b'\xef\xbb\xbf' + AUBERGINE,  # a BOM, as a hand-saved file has it
    AUBERGINE.replace(b'\n', b'\r\n'),
    AUBERGINE + b'\n\r\n\n',  # empty lines after end
    AUBERGINE[:-1],  # no line end after end
    AUBERGINE[:-1] + b'\r',
    theme_file(b'accent #C04A8A', b'battery inside'),  # upper-case hex
])
def test_a_whole_file_gives_its_accent_and_battery(fw, ct, data):
    assert parse(fw, data) == 1
    assert ct('accent').value == 1 << 24 | 0xc04a8a and ct('battery', C.c_int).value == 1 + STYLES.index('inside')
    assert ct('bg').value == (1 << 24 | 0x1e1226 if b'color' in data else 0)


@pytest.mark.parametrize('line', [b'accent c04a8a', b'accent xc04a8a', b'accent #c04a8', b'accent #c04a8a0', b'accent #c04g8a',
                                  b'accent #c04a8a x', b'accentx #c04a8a', b'accent#c04a8a', b'accent', b'battery', b'battery Both',
                                  b'battery both inside', b'battery full', b'batteryboth', b'batteries both',
                                  # only as the Studio writes them: no tab, no leading, trailing or second space
                                  b'  accent\t#C04A8A  ', b' accent #c04a8a', b'accent\t#c04a8a', b'accent  #c04a8a',
                                  b'accent #c04a8a ', b'battery   inside', b' battery inside', b'battery\tinside',
                                  b'battery inside ', b'battery inside\t',
                                  b'accent #c04a8a\r\r', b'accent #c04a8a\rx', b'battery inside\r\r',  # a CR not before its LF
                                  b'color  bg #1e1226', b'color bg  #1e1226', b'color fg #1e1226', b'color bg #12345',
                                  b'color bg #1234567', b'color bg #1e1226 ', b'color\tbg #1e1226', b'color bg\t#1e1226',
                                  b' color bg #1e1226', b'color bg 1e1226', b'colour bg #1e1226', b'color bg #1e1226\r\r',
                                  b'color bg', b'color #1e1226'])
def test_a_line_out_of_shape_is_skipped(fw, ct, line):
    assert parse(fw, theme_file(line)) == 1 and ct('accent').value == ct('bg').value == 0 and ct('battery', C.c_int).value == 0


@pytest.mark.parametrize('nl', [b'\n', b'\r\n'])
@pytest.mark.parametrize('line, bg', [(b'color bg #1e1226', 0x1e1226), (b'color bg #1E1226', 0x1e1226), (b'color bg #aBcDeF', 0xabcdef)])
def test_the_page_colour_is_read_as_the_studio_writes_it(fw, ct, nl, line, bg):
    assert parse(fw, theme_file(b'color bg #000000', line, b'color bg #12345', nl=nl)) == 1  # the last whole one
    assert ct('bg').value == 1 << 24 | bg and ct('accent').value == 0


def studio(data):
    """What Q2 Theme Studio's own reader (parse, docs/core.js) takes from a file: its accent, page colour and battery
    style, or None where it refuses the file."""
    text = data.decode('latin-1').removeprefix('\xef\xbb\xbf')
    lines = [line.removesuffix('\r') for line in text.split('\n')]
    while lines and lines[-1] == '': lines.pop()
    if len(lines) < 2 or lines[0] != 'q2theme 1' or lines[-1] != 'end': return None
    accent = bg = battery = 0
    for line in lines[1:-1]:
        word, *rest = line.split(' ')
        if word == 'accent' and len(rest) == 1 and re.fullmatch('#[0-9a-fA-F]{6}', rest[0]): accent = 1 << 24 | int(rest[0][1:], 16)
        if word == 'color' and len(rest) == 2 and rest[0] == 'bg' and re.fullmatch('#[0-9a-fA-F]{6}', rest[1]):
            bg = 1 << 24 | int(rest[1][1:], 16)
        if word == 'battery' and len(rest) == 1 and rest[0] in STYLES: battery = 1 + STYLES.index(rest[0])
    return accent, bg, battery


def test_the_parse_takes_what_the_studios_reader_takes(fw, ct):
    rng = random.Random(1)
    heads = [b'q2theme 1', b'q2theme 1', b'q2theme 1', b'\xef\xbb\xbfq2theme 1', b'q2theme 1 ', b'q2theme 2', b'']
    lines = [b'accent #c04a8a', b'accent #C04A8A', b'accent #c04a8', b'accent #c04a8a0', b'accent  #c04a8a', b'accent #c04a8a ',
             b' accent #c04a8a', b'accent\t#c04a8a', b'accent #c04a8a\rx', b'battery inside', b'battery both', b'battery icon',
             b'battery percent', b'battery bothx', b'battery  both', b'battery both ', b'battery', b'name A', b'color bg #1e1226',
             b'color bg #1E1226', b'color  bg #1e1226', b'color bg  #1e1226', b'color fg #1e1226', b'color bg #12345',
             b'color\tbg #1e1226', b'color bg #1e1226 ', b'end', b'q2theme 1', b'', b' ', b'\xa0accent #c04a8a',
             b'accent #c04a8a\xa0']
    ends = [b'\n', b'\n', b'\n', b'\r\n', b'\r\n', b'\r', b'\r\r\n', b'\n\n', b' \n', b'\t\n', b'']
    tails = [b'end', b'end', b'end', b'end ', b'End', b'', b'end\n ', b'end\n\x0c']
    seen = set()
    for _ in range(12000):
        parts = [rng.choice(heads), *rng.choices(lines, k=rng.randrange(9)), rng.choice(tails)]
        data = b''.join(part + rng.choice(ends) for part in parts)
        want = studio(data)
        seen.add(want)
        got = (ct('accent').value, ct('bg').value, ct('battery', C.c_int).value) if parse(fw, data) else None
        assert got == want, data
    assert len(seen) == 21  # refused, and each of accent or none and page colour or none with a style or none


def test_a_hex_digit_is_one_of_22_bytes(fw, ct):
    for byte in range(1, 256):
        parse(fw, theme_file(b'accent #12345' + bytes([byte])))
        want = int(chr(byte), 16) if chr(byte) in '0123456789abcdefABCDEF' else None
        assert ct('accent').value == (0 if want is None else 1 << 24 | 0x123450 | want), byte


def test_every_other_line_is_skipped_and_the_last_of_each_counts(fw, ct):
    data = theme_file(b'name A very long name', b'author Me', b'accent #112233', b'color bg #ffffff', b'home rows 40',
                      b'homeart 5 jg', b'page playing_page', b'hide btn_x', b'move view 1 2', b'size view 3 4', b'font v 9',
                      b'text v some words here', b'tint v #ffffff', b'future verb', b'battery icon', b'accent #445566',
                      b'battery percent')
    assert parse(fw, data) == 1
    assert ct('accent').value == 1 << 24 | 0x445566 and ct('battery', C.c_int).value == 1 + STYLES.index('percent')
    assert ct('bg').value == 1 << 24 | 0xffffff


def test_fuzz_card_theme(fw, ct):
    """card_theme_parse over damaged files, as card_theme_load hands it the copy: a heap block of exactly its bytes and
    read_all's NUL. It comes back, and what it keeps is one of the four styles, an accent or none and a page colour or
    none."""
    seeds = [AUBERGINE, b'\xef\xbb\xbf' + AUBERGINE.replace(b'\n', b'\r\n'), theme_file(b'battery both', b'accent #FFFFFF'),
             theme_file(), theme_file(b'page playing_page', b'hide img_cover', b'move scrlabel_title -12 99999',
                                      b'size label_ipod_album 100 30', b'font scrlabel_artist 30', b'page saverscreen_page',
                                      b'move img_slide 5 9', b'hide clock_time', b'page home_page', b'hide btn_playing',
                                      'text btn_folder Álbumes'.encode(), b'font btn_settings 24', b'homeart 12 jgj')]
    for data in fuzz.cases('card_theme', seeds):
        assert parse(fw, data) in (0, 1)
        accent, bg, battery = ct('accent').value, ct('bg').value, ct('battery', C.c_int).value
        assert 0 <= battery <= 4 and (accent == 0 or accent >> 24 == 1) and (bg == 0 or bg >> 24 == 1)


# The copy at a card mount (logo_sync's thread)
def sync(fw):
    fw.fn('card_theme_sync', None)()


def said(fw, seen):
    """card_theme_poll's toast, if any."""
    before = len(seen)
    fw.fn('card_theme_poll', None)()
    return seen[before].decode() if len(seen) > before else None


@pytest.fixture
def card(fw):
    Clock(fw)
    return Card(fw, {})


def test_a_whole_file_is_saved_once(fw, card):
    seen = toasts(fw)
    card.files[SRC] = AUBERGINE
    sync(fw)
    assert card.files[DST] == AUBERGINE and DST + '.tmp' not in card.files
    assert said(fw, seen) == 'Card theme saved. Restart to apply'
    opened = len(fw.calls('fw_fopen'))
    sync(fw)
    assert [C.string_at(c[1][1]) for c in fw.calls('fw_fopen')[opened:]] == [b'rb', b'rb']  # read both, wrote nothing
    assert said(fw, seen) is None


def test_a_bom_is_kept_in_the_copy(fw, card):
    card.files[SRC] = b'\xef\xbb\xbf' + AUBERGINE
    sync(fw)
    assert card.files[DST] == card.files[SRC]


@pytest.mark.parametrize('there', [(DST, BAD, TRY, SEEN), (DST,), (BAD,)])
def test_no_file_on_a_mounted_card_removes_the_copy_and_its_marks(fw, card, there):
    seen = toasts(fw)
    card.files.update({f: AUBERGINE for f in there})
    sync(fw)
    assert not {DST, BAD, TRY, SEEN} & set(card.files)
    assert said(fw, seen) == 'Card theme removed. Restart to finish'
    sync(fw)
    assert said(fw, seen) is None and not fw.calls('fw_fopen')  # nothing there: nothing to say, nothing read


def test_with_no_card_mounted_nothing_is_touched(fw, card):
    card.files[DST] = AUBERGINE
    Mounts(fw, card, mounted=False)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    logo_poll(fw)
    assert card.files[DST] == AUBERGINE


def test_the_mount_syncs_it_with_the_logo(fw, card):
    card.files[SRC] = AUBERGINE
    Mounts(fw, card)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    logo_poll(fw)
    assert card.files[DST] == AUBERGINE


def test_a_refused_file_keeps_the_copy_and_is_told_once(fw, card):
    seen = toasts(fw)
    card.files.update({SRC: b'not a theme', DST: AUBERGINE})
    sync(fw)
    assert card.files[DST] == AUBERGINE and said(fw, seen) == "Card theme file can't be used"
    sync(fw)
    assert said(fw, seen) is None  # the same file at the next mount
    card.files[SRC] = b'q2theme 1\naccent #000000\n'  # another: another size
    sync(fw)
    assert said(fw, seen) == "Card theme file can't be used"
    card.files[SRC] = AUBERGINE  # a good one forgets the refused
    sync(fw)
    assert SEEN not in card.files


def test_the_set_aside_bytes_are_not_copied_back(fw, card):
    seen = toasts(fw)
    card.files.update({SRC: AUBERGINE, BAD: AUBERGINE})
    sync(fw)
    assert DST not in card.files and card.files[BAD] == AUBERGINE and said(fw, seen) is None
    card.files[SRC] = theme_file(b'accent #112233')  # changed bytes: a new try, the marks gone
    card.files[TRY] = b'2'
    sync(fw)
    assert card.files[DST] == card.files[SRC] and BAD not in card.files and TRY not in card.files


def test_over_16_kb_is_refused_at_the_mount_and_at_the_start(fw, card, ct):
    seen = toasts(fw)
    big = b'q2theme 1\naccent #c04a8a\nend'.ljust(16384, b'\n')  # exactly 16 KB: taken
    card.files[SRC] = big
    sync(fw)
    assert card.files[DST] == big and said(fw, seen) == 'Card theme saved. Restart to apply'
    card.files[SRC] = big + b'\n'  # over, though only an empty line
    sync(fw)
    assert card.files[DST] == big and said(fw, seen) == "Card theme file can't be used"
    card.files[DST] = big + b'\n'
    start(fw)
    assert ct('accent').value == 0 and TRY not in card.files


def test_a_save_that_fails_says_so_and_leaves_no_tmp(fw, card):
    seen = toasts(fw)
    card.files[SRC], card.fail_rename = AUBERGINE, True
    sync(fw)
    assert DST not in card.files and DST + '.tmp' not in card.files and said(fw, seen) == 'Could not save card theme'


# The start: the guard and the accent
def start(fw, **config):
    Config(fw, **{k: str(v).encode() for k, v in config.items()})
    return fw.fn('accent', C.c_int)()


def test_the_count_is_written_before_the_theme_is_used_and_cleared_after_15_s(fw, card, ct):
    clock = Clock(fw)
    card.files[DST] = AUBERGINE
    start(fw)
    assert card.files[TRY] == b'1' and ct('accent').value
    clock.now += 14000
    fw.fn('card_theme_poll', None)()
    assert TRY in card.files
    clock.now += 1001  # 15 s from the start's time (made odd: 0 is none)
    fw.fn('card_theme_poll', None)()
    assert TRY not in card.files and ct('try_at').value == 0


def test_the_power_poll_clears_the_count_and_says_the_note(fw, card):
    clock, seen = Clock(fw), toasts(fw)
    card.files[DST] = AUBERGINE
    start(fw)
    card.files[SRC] = b'not a theme'
    sync(fw)  # a note waiting
    clock.now += 15001
    fw.fn('power_poll', None)()
    assert TRY not in card.files and b"Card theme file can't be used" in seen


def test_a_clean_end_clears_the_count(fw, card):
    card.files[DST] = AUBERGINE
    start(fw)
    fw.fn('ringnav_clean_end', None)()
    assert TRY not in card.files


def test_a_count_from_one_failed_start_goes_on(fw, card, ct):
    card.files.update({DST: AUBERGINE, TRY: b'1'})
    start(fw)
    assert card.files[TRY] == b'2' and ct('accent').value


def test_the_second_failed_start_sets_the_theme_aside(fw, card, ct):
    seen = toasts(fw)
    card.files.update({DST: AUBERGINE, TRY: b'2'})
    start(fw, BATTERY=0)
    assert card.files[BAD] == AUBERGINE and DST not in card.files and TRY not in card.files
    assert ct('accent').value == 0 and fw.fn('ipod_battery', C.c_int)() == 0
    assert said(fw, seen) == 'Card theme turned off after a failed start'


def test_a_start_that_cant_keep_count_runs_without_the_theme(fw, card, ct):
    card.files[DST], card.fail_rename = AUBERGINE, True
    start(fw, BATTERY=0)
    assert ct('accent').value == 0 and fw.fn('ipod_battery', C.c_int)() == 0 and TRY + '.tmp' not in card.files


def test_a_file_with_nothing_to_apply_is_not_counted(fw, card):
    card.files[DST] = theme_file(b'name Only a name')
    start(fw)
    assert TRY not in card.files


def test_a_page_colour_alone_is_counted(fw, card):
    card.files[DST] = theme_file(b'name Only a page', b'color bg #123456')
    start(fw)
    assert card.files[TRY] == b'1'


@pytest.mark.parametrize('line', [b'accent #c04a8a', b'color bg #1e1226'])
@pytest.mark.parametrize('theme', ['classic-light', 'classic-modern'])
@pytest.mark.parametrize('tried', [None, b'2'])  # a first start, or one after two failed ones
def test_under_classic_an_accent_or_a_page_alone_is_not_counted(fw, card, ct, line, theme, tried):
    seen = toasts(fw)
    card.files[DST] = theme_file(line)
    if tried:
        card.files[TRY] = tried
    start(fw, THEME=INDEX[theme])
    assert card.files.get(TRY) == tried and BAD not in card.files and ct('accent').value == ct('bg').value == 0
    assert card.files[DST] == theme_file(line) and said(fw, seen) is None


@pytest.mark.parametrize('theme', ['classic-light', 'classic-modern'])
def test_under_classic_an_accent_with_a_battery_is_counted(fw, card, theme):
    card.files[DST] = theme_file(b'accent #c04a8a', b'battery icon')
    start(fw, THEME=INDEX[theme])
    assert card.files[TRY] == b'1'


def scheme(fw):
    """scheme()'s tones and flags (scheme_t: the tones pointer, then flags) and its fill."""
    s = fw.fn('scheme', C.c_void_p)()
    return list((C.c_uint * 5).from_address(C.c_void_p.from_address(s).value or 0)), C.c_ubyte.from_address(s + 8).value, s


def card_start(fw, card, accent=b'#c04a8a', **config):
    card.files[DST] = theme_file(b'accent ' + accent, b'battery both')
    start(fw, **config)


@pytest.mark.parametrize('theme', ['minimal', 'modern'])
@pytest.mark.parametrize('light', [0, 1])
def test_minimal_and_modern_take_the_accent_in_either_half(fw, card, st, theme, light):
    card_start(fw, card, THEME=INDEX[theme], LIGHT=light)
    want = tones(0xc04a8a)
    assert scheme(fw)[:2] == (want, 0)
    a = fw.fn('accent', C.c_int)()
    accent_map = fw.fn('accent_map', C.c_uint, C.c_uint, C.c_int, C.c_int)
    assert accent_map(rgba(STOCK_RED), a, 3) == rgba(want[3])
    tone = fw.fn('accent_tone', C.c_uint, C.c_int)
    assert [tone(i) for i in range(5)] == want
    assert fw.fn('navigation__tones_of', C.POINTER(C.c_uint * 5), C.c_int)(a).contents[:] == want


@pytest.mark.parametrize('light', [0, 1])
def test_a_red_accents_own_tone_is_not_mapped_again(fw, card, light):
    card_start(fw, card, b'#e8123f', THEME=INDEX['minimal'], LIGHT=light)
    light_tone = tones(0xe8123f)[2]
    accent_map = fw.fn('accent_map', C.c_uint, C.c_uint, C.c_int, C.c_int)
    assert accent_map(rgba(light_tone), fw.fn('accent', C.c_int)(), 2) == rgba(light_tone)


def test_a_grey_accents_silver_still_flips_in_the_light_half(fw, card):
    card_start(fw, card, b'#8c8c8c', THEME=INDEX['minimal'], LIGHT=1)
    accent_map = fw.fn('accent_map', C.c_uint, C.c_uint, C.c_int, C.c_int)
    got = accent_map(rgba(0xd8d8d8), fw.fn('accent', C.c_int)(), 2)
    assert got == fw.fn('ui_ink', C.c_uint, C.c_uint)(rgba(0xd8d8d8)) != rgba(0xd8d8d8)


@pytest.mark.parametrize('stored', [dict(MINIMAL_ACCENT=IVORY), dict(MINIMAL_ACCENT=CRIMSON), dict(THEME=2, ACCENT=CRIMSON)])
def test_ivorys_card_and_crimsons_reds_give_way_to_the_accent(fw, card, stored):
    card_start(fw, card, **stored)
    assert scheme(fw)[1] == 0
    tone = fw.fn('accent_tone', C.c_uint, C.c_int)
    assert tone(2) == tones(0xc04a8a)[2]  # not Ivory's fill
    accent_map = fw.fn('accent_map', C.c_uint, C.c_uint, C.c_int, C.c_int)
    assert accent_map(rgba(STOCK_RED), fw.fn('accent', C.c_int)(), 3) == rgba(tones(0xc04a8a)[3])  # not Crimson's own


@pytest.mark.parametrize('light', [0, 1])
@pytest.mark.parametrize('theme', ['classic-light', 'classic-modern'])
def test_classic_and_classic_modern_keep_their_colours(fw, card, ct, scheme_at, theme, light):
    card.files[DST] = AUBERGINE
    start(fw, THEME=INDEX[theme], LIGHT=light)
    s = fw.fn('scheme', C.c_void_p)()
    assert s != C.addressof(ct('row', C.c_char))  # never the card's copy
    a = fw.fn('accent', C.c_int)()
    t = next(x for x in THEMES if x['id'] == theme)
    assert a == host.tools_theme.accent_default(t)
    assert scheme(fw)[0] != tones(0xc04a8a)
    assert fw.fn('ipod_light', C.c_int)() == light
    paper = C.c_uint.from_address(fw.fn('theme_engine__light_row', C.c_void_p)() + scheme_at['paper']).value
    assert fw.fn('flip', C.c_uint, C.c_uint)(0xff000000) == rgba(paper)  # the theme's own light paper


def own_row(fw, library, scheme_at):
    """The live theme's own dark row for the stored accent: theme_t.schemes[accent()]."""
    at = host.offsets(library.parent/'theme_engine.o', 'theme_t')['schemes']
    return C.c_void_p.from_address(fw.fn('theme', C.c_void_p)() + at).value + \
        fw.fn('accent', C.c_int)() * (scheme_at['label'] + C.sizeof(C.c_void_p))  # label: scheme_t's last field


@pytest.mark.parametrize('theme', ['classic-light', 'classic-modern'])
def test_nothing_but_the_battery_under_a_theme_without_the_flag(fw, library, card, ct, st, ct_fields, scheme_at, theme):
    clock = Clock(fw)
    card.files[DST] = AUBERGINE
    start(fw, THEME=INDEX[theme])
    clock.now += 15001
    fw.fn('card_theme_poll', None)()  # the count off: try_at back to 0
    assert fw.fn('scheme', C.c_void_p)() == own_row(fw, library, scheme_at)
    size = ct_fields['row'] + scheme_at['label'] + C.sizeof(C.c_void_p)  # row, a scheme_t (label last), is ct's last
    raw = bytearray(C.string_at(C.addressof(ct('accent', C.c_char)) - ct_fields['accent'], size))
    battery = ct_fields['battery']
    assert int.from_bytes(raw[battery:battery + 4], 'little') == 1 + STYLES.index('inside')
    raw[battery:battery + 4] = bytes(4)
    assert raw == bytes(size) and st('card_page').value == 0


def test_the_row_follows_a_theme_pick_and_light_mode(fw, card, st):
    def fill():  # scheme_t.fill, after tones, flags and mode: each theme's own row's
        return C.c_uint.from_address(fw.fn('scheme', C.c_void_p)() + 12).value
    card_start(fw, card, THEME=INDEX['minimal'])
    minimal = scheme(fw), fill()
    st('theme').value = INDEX['modern']
    modern = scheme(fw), fill()
    assert modern[0][:2] == minimal[0][:2] == (tones(0xc04a8a), 0)
    assert minimal[1] == O['MINIMAL_FILL'] and modern[1] == 0
    st('light').value = 1
    assert scheme(fw)[:2] == (tones(0xc04a8a), 0)  # a pick into Classic restarts: settings_read again


def top(ct):
    """The accent's first tone, as load built it."""
    return ct('tones').value


def five(ct):
    """The accent's five tones, as load built them."""
    return ct('tones', C.c_uint * 5)[:]


def test_an_accent_white_text_cant_be_read_on_is_darkened_to_3_to_1(fw, card, ct):
    Config(fw)
    load = fw.fn('card_theme_load', None)
    samples = [0xffffff, 0xfefefe, 0xffff00, 0x00ffff, 0xff00ff, 0xd8d8d8, 0x7fff00, 0xff8000, 0x808080, 0x1e90ff]
    samples += [r << 16 | g << 8 | b for r in range(0, 256, 51) for g in range(0, 256, 51) for b in range(0, 256, 51)]
    for rgb in samples:
        card.files[DST] = theme_file(b'accent #%06x' % rgb)
        card.files.pop(TRY, None)
        load()
        got = top(ct)
        assert five(ct) == tones(got), hex(rgb)
        assert on_white(got) >= 3, hex(rgb)
        if on_white(rgb) >= 3.25:  # readable already: as it is
            assert got == rgb, hex(rgb)
        assert all((got >> s & 255) <= (rgb >> s & 255) for s in (0, 8, 16))  # only ever darker


# Q2 Theme Studio's sample themes (studio/themes/*.txt), line for line
SAMPLES = {
    'aubergine': (b'Aubergine', 0xc04a8a, 0x1e1226, 'inside'),
    'deep-sea': (b'Deep Sea', 0x13838d, 0x0c1f2c, 'both'),
    'ember': (b'Ember', 0xc45400, 0x1c1210, 'percent'),
    'forest': (b'Forest', 0x647f34, 0x12241a, 'both'),
    'midnight': (b'Midnight', 0x2a6ad8, 0x101820, 'percent'),
    'paper': (b'Paper', 0x903868, 0xf4efe6, 'percent'),
}


@pytest.mark.parametrize('theme', ['minimal', 'modern'])
@pytest.mark.parametrize('name', SAMPLES)
def test_the_studios_samples_are_used_as_they_are(fw, card, ct, scheme_at, theme, name, st):
    """Each sample's accent, page colour, half and ink: the file's own, as the Studio shows it."""
    title, accent, bg, battery = SAMPLES[name]
    card.files[DST] = theme_file(b'name ' + title, b'author Q2 Pod', b'accent #%06x' % accent, b'color bg #%06x' % bg,
                                 b'battery ' + battery.encode())
    start(fw, THEME=INDEX[theme])
    assert top(ct) == accent and ct('battery', C.c_int).value == 1 + STYLES.index(battery)
    light = studio_ink(bg) != 0xffffff
    assert ct('bg').value == 1 << 24 | bg and st('card_page').value == 1 + light
    assert fw.fn('ipod_light', C.c_int)() == light
    assert paper_ink(fw, scheme_at) == (bg, studio_ink(bg)) and scheme(fw)[0] == tones(accent)


# The battery
@pytest.mark.parametrize('style', STYLES)
@pytest.mark.parametrize('theme', ['minimal', 'modern'])
def test_each_style_is_the_battery_in_minimal_and_modern(fw, card, theme, style):
    card.files[DST] = theme_file(b'battery ' + style.encode())
    start(fw, THEME=INDEX[theme], BATTERY=0)
    assert fw.fn('ipod_battery', C.c_int)() == STYLES.index(style)


@pytest.mark.parametrize('theme', ['classic-light', 'classic-modern'])
@pytest.mark.parametrize('style, shown', [('icon', 'icon'), ('percent', 'percent'), ('both', 'inside'), ('inside', 'inside')])
def test_classic_reads_both_as_its_own_default(fw, card, theme, style, shown):
    card.files[DST] = theme_file(b'battery ' + style.encode())
    start(fw, THEME=INDEX[theme], BATTERY=1)
    assert fw.fn('ipod_battery', C.c_int)() == STYLES.index(shown)


# The Display rows
def labels(page, *titles):
    """The rows' readings, title: value, as a theme with values of their own (Classic's set_text) splits them too."""
    return [': '.join(filter(None, row_of(button_of(page.view(), t).labels()[0]))) for t in titles]


@pytest.mark.parametrize('theme', ['minimal', 'modern'])
def test_the_rows_say_the_card_theme_sets_them_and_a_press_changes_nothing(fw, card, theme):
    card.files[DST] = AUBERGINE
    ui = Ui(fw)
    seen = toasts(fw)
    page = appearance(fw, ui, THEME=str(INDEX[theme]).encode(), BATTERY=b'0')
    rows = ['Accent: Card Theme', 'Battery: Card Theme', 'Light Mode: Card Theme']
    assert labels(page, 'Accent', 'Battery', 'Light Mode') == rows
    for title in ('Accent', 'Battery', 'Light Mode'):
        ui.click(button_of(page.view(), title))
        assert seen[-1] == b'Set by the card theme'
    assert writes(fw) == [] and not fw.calls('toolsWriteConfig')
    assert labels(page, 'Accent', 'Battery', 'Light Mode') == rows
    assert fw.fn('ipod_battery', C.c_int)() == STYLES.index('inside')


def test_under_classic_only_the_battery_row_is_the_card_themes(fw, card):
    card.files[DST] = AUBERGINE
    ui = Ui(fw)
    page = appearance(fw, ui, THEME=str(INDEX['classic-light']).encode())
    assert labels(page, 'Accent', 'Battery', 'Light Mode') == ['Accent: Classic Blue', 'Battery: Card Theme', 'Light Mode: Off']
    ui.click(button_of(page.view(), 'Accent'))
    assert [k for k, _ in writes(fw)] == ['CLASSIC_LIGHT_ACCENT']  # Accent is Classic's own row still


# No file: exactly as before
@pytest.mark.parametrize('theme', [t['id'] for t in THEMES])
def test_with_no_file_every_answer_is_the_stored_ones(fw, card, ct, scheme_at, theme, st):
    start(fw, THEME=INDEX[theme], BATTERY=2, LIGHT=1)
    s = fw.fn('scheme', C.c_void_p)()
    assert s != C.addressof(ct('row', C.c_char)) and ct('base', C.c_void_p).value is None
    assert st('card_page').value == 0 and fw.fn('ipod_light', C.c_int)() == 1
    assert fw.fn('flip', C.c_uint, C.c_uint)(0xff000000) == rgba(C.c_uint.from_address(s + scheme_at['paper']).value)
    t = next(x for x in THEMES if x['id'] == theme)
    assert fw.fn('ipod_battery', C.c_int)() == (STYLES.index('inside') if theme.startswith('classic') else 2)
    assert fw.fn('accent', C.c_int)() == host.tools_theme.accent_default(t)
    assert [C.string_at(c[1][0]).decode() for c in fw.calls('fw_fopen')] == [DST]  # one look for the copy, nothing written
    assert TRY not in card.files and not fw.calls('fw_unlink') and not fw.calls('toolsWriteConfig')


# The flip on a page: per channel
@pytest.fixture(scope='module')
def scheme_at(library):
    return host.offsets(library.parent/'theme_engine.o', 'scheme_t')


def studio_ink(bg):
    """Q2 Theme Studio's ink on a page (palette, docs/core.js): #161616 at a luma of 125 or more, else white."""
    return 0x161616 if ((bg >> 16) * 299 + (bg >> 8 & 255) * 587 + (bg & 255) * 114) // 1000 >= 125 else 0xffffff


# Q2 Theme Studio's own flip (docs/core.js flip, run under node) of FLIPPED's colours on each page: the six samples, the
# greys either side of its light threshold and a green, each channel to the page's own
FLIPPED = (0x000000, 0x1c1c1c, 0x2c2c2e, 0x424242, 0x808080, 0xaaaaaa, 0xffffff, 0x101820, 0x1c1210, 0xf4efe6, 0x2a6ad8)
STUDIO_FLIPS = {
    0x101820: '101820 2a3138 393f48 4d5359 878b8f afb2b4 ffffff 1e2d3b 2a282d f4f0e9 2a6ad8',
    0x0c1f2c: '0c1f2c 263743 354552 4a5862 858f95 aeb4b8 ffffff 1b3446 262e39 f4f0ea 2a6ad8',
    0x1c1210: '1c1210 342c2a 433a3b 564f4d 8d8887 b3b0af ffffff 2a282d 34221e f5f0e7 2a6ad8',
    0x12241a: '12241a 2c3c33 3a4943 4f5c55 88918c b0b6b2 ffffff 203836 2c3328 f4f1e8 2a6ad8',
    0x1e1226: '1e1226 362c3d 443a4d 584f5e 8e8892 b4b0b6 ffffff 2c2841 362233 f5f0e9 2a6ad8',
    0xf4efe6: 'f4efe6 dcd8d0 cecac1 bbb7b1 85837e 605f5c 161616 e7dbcc dce0d9 20242b 2a6ad8',
    0x7c7c7c: '7c7c7c 8a8a8a 929293 9d9d9d bdbdbd d3d3d3 ffffff 84888c 8a8584 f9f6f2 2a6ad8',
    0x7d7d7d: '7d7d7d 727272 6c6c6b 636363 4a4a4a 393939 161616 777471 727677 1b1d21 2a6ad8',
    0x00c000: '00c000 1cc61c 2cca2e 42d042 80df80 aaeaaa ffffff 10c520 1cc410 f4fbe6 2a6ad8',
}


@pytest.mark.parametrize('bg', STUDIO_FLIPS)
def test_the_flip_on_a_coloured_paper_is_the_studios(fw, card, ct, st, scheme_at, bg):
    """flip (a color_t) and flip_rgb (a 0xRRGGBB) on the card's row with bg as its paper, each in its own byte order."""
    card_start(fw, card, THEME=INDEX['minimal'], LIGHT=1)
    st('card_page').value = 2  # a page: the flip reads the card's row
    row = C.addressof(ct('row', C.c_char))
    assert fw.fn('scheme', C.c_void_p)() == row
    C.c_uint.from_address(row + scheme_at['paper']).value = bg
    C.c_uint.from_address(row + scheme_at['ink']).value = studio_ink(bg)
    flip, flip_rgb = fw.fn('flip', C.c_uint, C.c_uint), fw.fn('flip_rgb', C.c_uint, C.c_uint)
    want = [int(h, 16) for h in STUDIO_FLIPS[bg].split()]
    assert [flip_rgb(c) for c in FLIPPED] == want
    assert [flip(rgba(c)) for c in FLIPPED] == [rgba(c) for c in want]
    assert fw.fn('flip_if', C.c_uint, C.c_int, C.c_uint)(1, 0x1c1c1c) == want[1]


# What main's code answered before the flip went per channel (ipod_light, accent, scheme()'s and light_row()'s rows,
# then ui_ink, flip, flip_if, accent_map and red_map over RAMP and unflip over 0-255), by theme, Light Mode and stored
# accent (the theme's default, else Crimson, Classic's second): their digest
RAMP = [0, 0xff000000, 0xffffffff, 0x80000000, 0xff808080, 0x80404040, 0xff102010, 0xff1c1c1c, 0xffe9e9ec, 0xff4814ff,
        0xffd8d8d8, 0xff73706e, 0xff9d9898, 0xff161616, 0xfff4f4f4, 0x00123456, rgba(STOCK_RED), rgba(STOCK_RED, 0x80),
        0xff8080ff, 0xff4040c0, 0xffc0c0ff, 0xff202060, 0xffa0a0e0]
BEFORE = {
    ('minimal', 0, 0): 'd3bad567ec077293', ('minimal', 0, 1): 'f9e60fd7c8a00b0c',
    ('minimal', 1, 0): 'e72d7c28e2c68dbd', ('minimal', 1, 1): '4f17d92bfed76a56',
    ('classic-light', 0, 0): '7941c4ffc6eb9bba', ('classic-light', 0, 1): 'f735c056a5c6a45c',
    ('classic-light', 1, 0): '630244df0c2b43d6', ('classic-light', 1, 1): '6f52ebdcd7c806d6',
    ('modern', 0, 0): 'f4bcd79fb1f2f919', ('modern', 0, 1): 'eedaa32b920ddbb0',
    ('modern', 1, 0): '61dc8a8c9a2e8140', ('modern', 1, 1): '8d6f42b6620c23ba',
    ('classic-modern', 0, 0): '7941c4ffc6eb9bba', ('classic-modern', 0, 1): 'f735c056a5c6a45c',
    ('classic-modern', 1, 0): '630244df0c2b43d6', ('classic-modern', 1, 1): '6f52ebdcd7c806d6',
}


@pytest.mark.parametrize('theme, light, other', BEFORE)
def test_with_no_file_the_flip_answers_as_before(fw, card, scheme_at, theme, light, other):
    t = next(x for x in THEMES if x['id'] == theme)
    stored = {t['accent_key']: CRIMSON if t['card_colors'] else 1} if other else {}
    a = start(fw, THEME=INDEX[theme], LIGHT=light, **stored)

    def row(p):
        fields = [C.c_uint.from_address(p + scheme_at[k]).value for k in ('fill', 'chevron', 'muted', 'bar_light', 'paper', 'ink')]
        tones = C.c_void_p.from_address(p + scheme_at['tones']).value or 0
        return [*fields, C.c_ubyte.from_address(p + scheme_at['flags']).value, *(C.c_uint * 5).from_address(tones)]
    out = [fw.fn('ipod_light', C.c_int)(), a, row(fw.fn('scheme', C.c_void_p)()), row(fw.fn('theme_engine__light_row', C.c_void_p)())]
    ui_ink, flip, flip_if = fw.fn('ui_ink', C.c_uint, C.c_uint), fw.fn('flip', C.c_uint, C.c_uint), fw.fn('flip_if', C.c_uint, C.c_int, C.c_uint)
    accent_map = fw.fn('accent_map', C.c_uint, C.c_uint, C.c_int, C.c_int)
    red_map = fw.fn('red_map', C.c_uint, C.c_uint, C.c_uint, C.c_uint, C.c_int)
    for c in RAMP:
        out += [ui_ink(c), flip(c), flip_if(1, c), accent_map(c, a, 2), accent_map(c, a, 3),
                red_map(c, 0x2a6ad8, 0xffffff, 0), red_map(c, 0x2a6ad8, 0xffffff, 1), red_map(c, 0x2a6ad8, 0x1c1c1c, 1)]
    out += [fw.fn('unflip', C.c_uint, C.c_uint)(v) for v in range(256)]
    assert hashlib.sha256(repr(out).encode()).hexdigest()[:16] == BEFORE[theme, light, other]
    flip_rgb = fw.fn('flip_rgb', C.c_uint, C.c_uint)
    assert all(flip_rgb(c) == flip(c) for c in RAMP)  # grey paper: either byte order


# The page: its half, its row, the clamp, and what follows it
MIDNIGHT, PAPER = 0x101820, 0xf4efe6
INK = O['LIGHT_INK'] * 0x010101


def page_start(fw, card, bg, theme='minimal', **config):
    card.files[DST] = theme_file(b'color bg #%06x' % bg)
    strings(fw)
    return start(fw, THEME=INDEX[theme], **config)


def paper_ink(fw, scheme_at):
    s = fw.fn('scheme', C.c_void_p)()
    return C.c_uint.from_address(s + scheme_at['paper']).value, C.c_uint.from_address(s + scheme_at['ink']).value


@pytest.mark.parametrize('theme', ['minimal', 'modern'])
@pytest.mark.parametrize('light', [0, 1, 2])
@pytest.mark.parametrize('bg, half', [(MIDNIGHT, 0), (PAPER, 1)])
def test_a_dark_page_keeps_the_dark_half_and_a_light_one_takes_the_light_half(fw, card, scheme_at, theme, light, bg, half, st):
    page_start(fw, card, bg, theme, LIGHT=light)
    assert fw.fn('ipod_light', C.c_int)() == half
    assert st('card_page').value == 1 + half
    ink = INK if half else 0xffffff
    assert paper_ink(fw, scheme_at) == (bg, ink) and fw.fn('flat_page', C.c_uint)() == bg
    flip = fw.fn('flip', C.c_uint, C.c_uint)
    assert flip(0xff000000) == rgba(bg) and flip(0xffffffff) == rgba(ink)
    assert fw.fn('ui_ink', C.c_uint, C.c_uint)(0xff000000) == rgba(bg)


def test_a_theme_pick_keeps_the_pages_half(fw, card, st, scheme_at):
    page_start(fw, card, MIDNIGHT, 'minimal')
    st('theme').value, st('light').value = INDEX['modern'], 1  # Modern's preset: Light Mode on
    assert fw.fn('ipod_light', C.c_int)() == 0 and paper_ink(fw, scheme_at) == (MIDNIGHT, 0xffffff)


@pytest.mark.parametrize('bg', [MIDNIGHT, PAPER, 0x1e1226])
def test_unflip_on_a_page_comes_back_within_a_step(fw, card, bg):
    """unflip's grey, flipped onto the page: v again in its channels' mean, else the end nearest v."""
    page_start(fw, card, bg)
    unflip, flip_rgb = fw.fn('unflip', C.c_uint, C.c_uint), fw.fn('flip_rgb', C.c_uint, C.c_uint)
    ink = studio_ink(bg) & 255
    lo, hi = sorted((sum(channels(bg)) // 3, ink))
    for v in range(256):
        u = unflip(v)
        if lo <= v <= hi:
            assert abs(sum(channels(flip_rgb(u * 0x010101))) / 3 - v) <= 2, v  # each channel truncated
        else:
            assert u == (255 if (v < lo) == (ink == lo) else 0), v


@pytest.mark.parametrize('theme, stored', [('minimal', dict(MINIMAL_ACCENT=IVORY)), ('minimal', dict(MINIMAL_ACCENT=CRIMSON)),
                                           ('modern', dict(ACCENT=CRIMSON))])
def test_a_page_alone_keeps_the_presets_tones_and_flags(fw, library, card, scheme_at, theme, stored):
    a = page_start(fw, card, MIDNIGHT, theme, **stored)
    own, s = own_row(fw, library, scheme_at), fw.fn('scheme', C.c_void_p)()
    assert s != own and C.c_void_p.from_address(s).value == C.c_void_p.from_address(own).value  # its tones
    assert C.c_ubyte.from_address(s + scheme_at['flags']).value == C.c_ubyte.from_address(own + scheme_at['flags']).value != 0
    if a == CRIMSON:  # half stock red over black: half red over the page, not as it is
        half = mix(STOCK_RED, 0, 1, 2)
        got = style_color(fw, rgba(half), b'bg_color')
        want = mix(fw.fn('accent_tone', C.c_uint, C.c_int)(2), MIDNIGHT, 1, 2)  # Crimson's own tone: stock red
        assert got != rgba(half)
        assert all(abs(g - w) <= 2 for g, w in zip(channels(rgb_of(got)), channels(want))), hex(got)
        to = fw.fn('flip_rgb', C.c_uint, C.c_uint)(fw.fn('accent_tone', C.c_uint, C.c_int)(2))  # the preset's tone, no card
        accent_map, red_map = fw.fn('accent_map', C.c_uint, C.c_uint, C.c_int, C.c_int), fw.fn('red_map', C.c_uint, C.c_uint, C.c_uint, C.c_uint, C.c_int)
        assert accent_map(rgba(to), a, 2) == red_map(rgba(to), to, 0xffffff, 1) != rgba(to)  # mapped, as in Light Mode


def quiet_on(c, quiet):
    """WCAG's contrast of quiet text on c."""
    a, b = sorted((luminance(c), luminance(quiet)))
    return (b + 0.05) / (a + 0.05)


def test_a_page_text_cant_be_read_on_is_clamped(fw, card, ct, st):
    """A dark page deepened, a light one paled, until the half's quiet line (flat_quiet) reads 3:1 on it."""
    Config(fw)
    load = fw.fn('card_theme_load', None)
    samples = [0xffffff, 0xfefefe, 0xffff00, 0x00ffff, 0xff00ff, 0xd8d8d8, 0x7fff00, 0xff8000, 0x808080, 0x1e90ff,
               0x7c7c7c, 0x7d7d7d, 0x00c000, 0x000000, 0x0000ff, 0xff0000, *(s[2] for s in SAMPLES.values())]
    samples += [r << 16 | g << 8 | b for r in range(0, 256, 51) for g in range(0, 256, 51) for b in range(0, 256, 51)]
    for rgb in samples:
        card.files[DST] = theme_file(b'color bg #%06x' % rgb)
        card.files.pop(TRY, None)
        load()
        got, light = ct('bg').value & 0xffffff, studio_ink(rgb) != 0xffffff
        assert ct('bg').value >> 24 == 1 and st('card_page').value == 1 + light, hex(rgb)
        quiet = 0x6e6e73 if light else 0x98989d
        assert quiet_on(got, quiet) >= 3, hex(rgb)
        if quiet_on(rgb, quiet) >= 3.25:  # readable already: as it is
            assert got == rgb, hex(rgb)
        darker = all((got >> s & 255) <= (rgb >> s & 255) for s in (0, 8, 16))
        paler = all((got >> s & 255) >= (rgb >> s & 255) for s in (0, 8, 16))
        assert paler if light else darker, hex(rgb)  # only ever paler, or deeper


@pytest.mark.parametrize('fmt', [1, 2, 3, 4])
def test_a_card_page_flips_a_plated_image_onto_its_colour(fw, card, bitmaps, fmt):
    """On a dark page every colourless image flips, a plate too: its black takes the page, in the bitmap's own byte
    order, and its white stays. A hued image stays as it is."""
    page_start(fw, card, MIDNIGHT, 'modern')
    plate = bitmaps(fw, [[px(0)] * 3 + [WHITE]], fmt=fmt)  # mostly dark: Light Mode keeps it
    image_add(fw, b'list_disc', plate)
    assert plate.rows() == [[px(MIDNIGHT)] * 3 + [WHITE]]
    hued = bitmaps(fw, [[px(0x2080ff), px(0)]], fmt=fmt)
    image_add(fw, b'list_more', hued)
    assert hued.rows() == [[px(0x2080ff), px(0)]]


def test_a_card_page_skips_autos_reload(fw, card):
    page_start(fw, card, MIDNIGHT, LIGHT=2)
    apply = fw.fn('light_apply', None, C.c_void_p)
    for hour in (7, 19, 12, 3):
        tm = (C.c_int * 11)()
        tm[2] = hour
        apply(tm)
    assert not fw.calls('image_manager_unload_all')


def test_modern_s_light_bar_takes_the_card_page(fw, card, world):
    page_start(fw, card, PAPER, 'modern')
    bar = system_bar(fw, world)
    canvas = Canvas(fw)
    paint_bg(fw, bar, canvas)
    assert canvas.fills[0] == (0, 0, 375, 26, rgba(PAPER))


def test_cover_flow_and_home_art_lie_on_the_page(fw, card):
    """A clear cover over the page's colour, each channel its own (Cover Flow's texels the screen's
    BGRA8888, Home's red low)."""
    page_start(fw, card, MIDNIGHT)
    texel = 0xff000000 | 0x10 | 0x18 << 8 | 0x20 << 16
    cover(fw, ART, ART, cover_pixels(ART, ART, lambda x, y: (0, 0, 0, 0)))
    assert decoded(fw)[1][0] == bgra(texel)
    tex, home, _ = tex_rig(fw, 300, 300, cover_pixels(300, 300, lambda x, y: (0, 0, 0, 0)))
    tex(b'x')
    assert (C.c_uint * 8).from_address(home.tex)[5] == texel
