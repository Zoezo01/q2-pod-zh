"""patch/card_theme.c: a card theme's whole-file check and its two lines (accent, battery), the card-to-flash copy at a
mount, the failed-start guard, the accent's scheme row under the themes that take it and the battery style in all four,
and the Display rows it sets."""
import ctypes as C
import random
import re

import pytest

import fuzz
import host
from conftest import O, block
from test_nav_display_library import Config, Ui, appearance, button_of, row_of, writes
from test_nav_loop import Card, Clock, Mounts, logo_poll, toasts

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


def rgba(c, a=0xff):
    return a << 24 | (c & 255) << 16 | (c & 0xff00) | c >> 16


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
    assert parse(fw, data) == 0 and ct('accent').value == 0 and ct('battery', C.c_int).value == 0


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


@pytest.mark.parametrize('line', [b'accent c04a8a', b'accent xc04a8a', b'accent #c04a8', b'accent #c04a8a0', b'accent #c04g8a',
                                  b'accent #c04a8a x', b'accentx #c04a8a', b'accent#c04a8a', b'accent', b'battery', b'battery Both',
                                  b'battery both inside', b'battery full', b'batteryboth', b'batteries both',
                                  # only as the Studio writes them: no tab, no leading, trailing or second space
                                  b'  accent\t#C04A8A  ', b' accent #c04a8a', b'accent\t#c04a8a', b'accent  #c04a8a',
                                  b'accent #c04a8a ', b'battery   inside', b' battery inside', b'battery\tinside',
                                  b'battery inside ', b'battery inside\t',
                                  b'accent #c04a8a\r\r', b'accent #c04a8a\rx', b'battery inside\r\r'])  # a CR not before its LF
def test_a_line_out_of_shape_is_skipped(fw, ct, line):
    assert parse(fw, theme_file(line)) == 1 and ct('accent').value == 0 and ct('battery', C.c_int).value == 0


def studio(data):
    """What Q2 Theme Studio's own reader (parse, docs/core.js) takes from a file: its accent and battery style, or None
    where it refuses the file."""
    text = data.decode('latin-1').removeprefix('\xef\xbb\xbf')
    lines = [line.removesuffix('\r') for line in text.split('\n')]
    while lines and lines[-1] == '': lines.pop()
    if len(lines) < 2 or lines[0] != 'q2theme 1' or lines[-1] != 'end': return None
    accent = battery = 0
    for line in lines[1:-1]:
        word, *rest = line.split(' ')
        if word == 'accent' and len(rest) == 1 and re.fullmatch('#[0-9a-fA-F]{6}', rest[0]): accent = 1 << 24 | int(rest[0][1:], 16)
        if word == 'battery' and len(rest) == 1 and rest[0] in STYLES: battery = 1 + STYLES.index(rest[0])
    return accent, battery


def test_the_parse_takes_what_the_studios_reader_takes(fw, ct):
    rng = random.Random(1)
    heads = [b'q2theme 1', b'q2theme 1', b'q2theme 1', b'\xef\xbb\xbfq2theme 1', b'q2theme 1 ', b'q2theme 2', b'']
    lines = [b'accent #c04a8a', b'accent #C04A8A', b'accent #c04a8', b'accent #c04a8a0', b'accent  #c04a8a', b'accent #c04a8a ',
             b' accent #c04a8a', b'accent\t#c04a8a', b'accent #c04a8a\rx', b'battery inside', b'battery both', b'battery icon',
             b'battery percent', b'battery bothx', b'battery  both', b'battery both ', b'battery', b'name A', b'color bg #1e1226',
             b'end', b'q2theme 1', b'', b' ', b'\xa0accent #c04a8a', b'accent #c04a8a\xa0']
    ends = [b'\n', b'\n', b'\n', b'\r\n', b'\r\n', b'\r', b'\r\r\n', b'\n\n', b' \n', b'\t\n', b'']
    tails = [b'end', b'end', b'end', b'end ', b'End', b'', b'end\n ', b'end\n\x0c']
    seen = set()
    for _ in range(6000):
        parts = [rng.choice(heads), *rng.choices(lines, k=rng.randrange(5)), rng.choice(tails)]
        data = b''.join(part + rng.choice(ends) for part in parts)
        want = studio(data)
        seen.add(want)
        got = (ct('accent').value, ct('battery', C.c_int).value) if parse(fw, data) else None
        assert got == want, data
    assert len(seen) == 11  # refused, and each of accent or none with a style or none


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


def test_fuzz_card_theme(fw, ct):
    """card_theme_parse over damaged files, as card_theme_load hands it the copy: a heap block of exactly its bytes and
    read_all's NUL. It comes back, and what it keeps is one of the four styles and an accent or none."""
    seeds = [AUBERGINE, b'\xef\xbb\xbf' + AUBERGINE.replace(b'\n', b'\r\n'), theme_file(b'battery both', b'accent #FFFFFF'),
             theme_file()]
    for data in fuzz.cases('card_theme', seeds):
        assert parse(fw, data) in (0, 1)
        accent, battery = ct('accent').value, ct('battery', C.c_int).value
        assert 0 <= battery <= 4 and (accent == 0 or accent >> 24 == 1)


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
    card.files[DST] = theme_file(b'name Only a name', b'color bg #123456')
    start(fw)
    assert TRY not in card.files


@pytest.mark.parametrize('theme', ['classic-light', 'classic-modern'])
@pytest.mark.parametrize('tried', [None, b'2'])  # a first start, or one after two failed ones
def test_under_classic_an_accent_alone_is_not_counted(fw, card, ct, theme, tried):
    seen = toasts(fw)
    card.files[DST] = theme_file(b'accent #c04a8a')
    if tried:
        card.files[TRY] = tried
    start(fw, THEME=INDEX[theme])
    assert card.files.get(TRY) == tried and BAD not in card.files and ct('accent').value == 0
    assert card.files[DST] == theme_file(b'accent #c04a8a') and said(fw, seen) is None


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


@pytest.mark.parametrize('theme', ['classic-light', 'classic-modern'])
def test_classic_and_classic_modern_keep_their_colours(fw, card, ct, theme):
    card_start(fw, card, THEME=INDEX[theme])
    assert fw.fn('scheme', C.c_void_p)() != C.addressof(ct('row', C.c_char))  # never the card's copy
    a = fw.fn('accent', C.c_int)()
    t = next(x for x in THEMES if x['id'] == theme)
    assert a == host.tools_theme.accent_default(t)
    assert scheme(fw)[0] != tones(0xc04a8a)


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


@pytest.mark.parametrize('name', SAMPLES)
def test_the_studios_sample_accents_are_used_as_they_are(fw, card, ct, name):
    title, accent, bg, battery = SAMPLES[name]
    card.files[DST] = theme_file(b'name ' + title, b'author Q2 Pod', b'accent #%06x' % accent, b'color bg #%06x' % bg,
                                 b'battery ' + battery.encode())
    start(fw)
    assert top(ct) == accent and ct('battery', C.c_int).value == 1 + STYLES.index(battery)


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
    assert labels(page, 'Accent', 'Battery') == ['Accent: Card Theme', 'Battery: Card Theme']
    for title in ('Accent', 'Battery'):
        ui.click(button_of(page.view(), title))
        assert seen[-1] == b'Set by the card theme'
    assert writes(fw) == [] and not fw.calls('toolsWriteConfig')
    assert labels(page, 'Accent', 'Battery') == ['Accent: Card Theme', 'Battery: Card Theme']
    assert fw.fn('ipod_battery', C.c_int)() == STYLES.index('inside')


def test_under_classic_only_the_battery_row_is_the_card_themes(fw, card):
    card.files[DST] = AUBERGINE
    ui = Ui(fw)
    page = appearance(fw, ui, THEME=str(INDEX['classic-light']).encode())
    assert labels(page, 'Accent', 'Battery') == ['Accent: Classic Blue', 'Battery: Card Theme']
    ui.click(button_of(page.view(), 'Accent'))
    assert [k for k, _ in writes(fw)] == ['CLASSIC_LIGHT_ACCENT']  # Accent is Classic's own row still


# No file: exactly as before
@pytest.mark.parametrize('theme', [t['id'] for t in THEMES])
def test_with_no_file_every_answer_is_the_stored_ones(fw, card, ct, theme):
    start(fw, THEME=INDEX[theme], BATTERY=2, LIGHT=1)
    s = fw.fn('scheme', C.c_void_p)()
    assert s != C.addressof(ct('row', C.c_char)) and ct('base', C.c_void_p).value is None
    t = next(x for x in THEMES if x['id'] == theme)
    assert fw.fn('ipod_battery', C.c_int)() == (STYLES.index('inside') if theme.startswith('classic') else 2)
    assert fw.fn('accent', C.c_int)() == host.tools_theme.accent_default(t)
    assert [C.string_at(c[1][0]).decode() for c in fw.calls('fw_fopen')] == [DST]  # one look for the copy, nothing written
    assert TRY not in card.files and not fw.calls('fw_unlink') and not fw.calls('toolsWriteConfig')
