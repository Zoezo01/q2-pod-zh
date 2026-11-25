"""patch/card_rules.c: a card theme's widget rules, read as Q2 Theme Studio's reader reads them (patch/card_widgets.inc
standing in for its catalogue), dropped wherever they don't apply, and applied to Now Playing (its iPod layout) and the
lock screen as they open and to Home's rows at each layout, and homeart's pictures on Home in Split, under Minimal and
Modern."""
import ctypes as C
import random
import re
import struct

import pytest

import host
from conftest import O
from test_card_theme import DST, INDEX, STYLES, TRY, card, parse, start, theme_file  # noqa: F401 (card: a fixture)
from test_coverflow import Bitmaps, Home
from test_coverflow import Ui as HomeUi
from test_lockscreen import Bitmaps as LockBitmaps
from test_lockscreen import Window
from test_nav_loop import Clock, jpeg
from test_nav_playing import MINIMAL, MODERN, Now, st, theme, ui  # noqa: F401 (st, ui: fixtures)

HIDE, MOVE, SIZE, TEXT = 1, 2, 4, 32  # card_rules.c's CR_*
PAGES = {'CARD_PLAYING': (0, 'playing_page'), 'CARD_LOCK': (1, 'saverscreen_page'), 'CARD_HOME': (2, 'home_page')}
ROWS = [(PAGES[m[1]], m[2], set(m[3].split(' | ')), int(m[4]), tuple(int(v) for v in m.groups()[4:])) for m in re.finditer(
    r'^CARD_WIDGET\((CARD_\w+), "(\w+)", ([A-Z_ |]+), (\d+), (-?\d+), (-?\d+), (-?\d+), (-?\d+)\)$',
    host.ROOT.joinpath('patch/card_widgets.inc').read_text(), re.M)]
HOME = next(i for i, (pg, *_) in enumerate(ROWS) if pg[1] == 'home_page')  # card_rules.c's: Home's rows, the last
VERBS = {'hide': (1, 'CR_HIDE'), 'move': (3, 'CR_MOVE'), 'size': (3, 'CR_SIZE'), 'font': (2, 'CR_FONT')}
JS_SPACE = '\t\n\v\f\r \xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'  # JS's \s, all trim() strips
W_EMITTER, EMIT_TYPE, EVT_CLICK = O['W_EMITTER'], O['EMIT_TYPE'], O['EVT_CLICK']
FAV_CLICK = 0x53c968  # Now Playing's heart: stock's My Fav toggle (ratings.c rate_bind)
# Every kind of line, on both pages: what the gate must drop whole.
EVERY = theme_file(b'page playing_page', b'hide img_cover', b'move scrlabel_title 10 20', b'size label_ipod_album 100 30',
                   b'font scrlabel_artist 30', b'hide slider_play', b'page saverscreen_page', b'hide clock_time',
                   b'move img_slide 5 9', b'page home_page', b'hide btn_playing', b'text btn_folder Albums',
                   b'font btn_settings 24', b'homeart 12 jgj', b'battery inside')


def rules(*lines, page=b'playing_page'):
    return theme_file(b'page ' + page, *lines)


@pytest.fixture(scope='module')
def cr_fields(library):
    return host.offsets(library.parent/'card_rules.o', 'cr')


def state(fw, fields):
    """cr per row: (flags, x, y, w, h, font, words)."""
    base, n = C.addressof(fw.var('card_rules__cr', C.c_char)), len(ROWS)
    flags, font = C.string_at(base + fields['flags'], n), C.string_at(base + fields['font'], n)
    box = (C.c_short * (4 * n)).from_address(base + fields['box'])
    words = [C.string_at(base + fields['words'] + 41 * (i - HOME)).decode() if flags[i] & TEXT else '' for i in range(n)]
    return [(flags[i], *box[4 * i:4 * i + 4], font[i], words[i]) for i in range(n)]


def whole_state(fw):
    """All of the rules' state: cr's bytes, card_home, and st.card_rules."""
    raw = C.string_at(C.addressof(fw.var('card_rules__cr', C.c_char)), fw.sizes['card_rules__cr'])
    return raw, fw.var('card_home', C.c_int).value, fw.fn('card_rules_used', C.c_int)()


def art_state(fw, fields):
    """homeart's as cr holds it: (seconds, its JPEGs' numbers), None with no JPEG."""
    base = C.addressof(fw.var('card_rules__cr', C.c_char))
    arts = C.c_ubyte.from_address(base + fields['arts']).value
    return (C.c_short.from_address(base + fields['secs']).value, list(C.string_at(base + fields['art'], arts))) if arts else None


def clean_text(text):
    """Q2 Theme Studio's cleanText (docs/core.js, cleanName in it): what it writes of a row's words."""
    def trim(t):
        return re.sub(f'^[{JS_SPACE}]+|[{JS_SPACE}]+$', '', t)
    plain = trim(trim(re.sub('[\x00-\x1f\x7f-\x9f\u2028\u2029]', '', re.sub(f'[{JS_SPACE}]+', ' ', text)))[:40])
    plain = trim(plain[:24])
    while len(plain.encode()) > 40:
        plain = trim(plain[:-1])
    return plain


def studio_words(raw):
    """A text line's words as Q2 Pod keeps them: as written where the Studio's cleanText leaves them so, which is how it
    writes them, else none (invalid UTF-8 too)."""
    try:
        words = raw.encode('latin-1').decode()
    except UnicodeDecodeError:
        return ''
    return words if words and clean_text(words) == words else ''


def studio_rules(data):
    """What Q2 Theme Studio's reader (docs/core.js parse, then clampBox, the font rule, cleanText and cleanArt of
    normalize) keeps of a file's rule lines, card_widgets.inc for its catalogue: per row (flags, x, y, w, h, font, words),
    the move as written (saturated at 30000), the size clamped, words only where cleanText leaves them; all 0 where it
    refuses the file. And its homeart, as art_state has it."""
    kept, art = [(0, 0, 0, 0, 0, 0, '')] * len(ROWS), None
    text = data.decode('latin-1').removeprefix('\xef\xbb\xbf')
    lines = [line.removesuffix('\r') for line in text.split('\n')]
    while lines and lines[-1] == '': lines.pop()
    if len(lines) < 2 or lines[0] != 'q2theme 1' or lines[-1] != 'end': return kept, art
    page = None
    for line in lines[1:-1]:
        word, *rest = line.split(' ')
        if word == 'homeart' and len(rest) == 2 and re.fullmatch('-?[0-9]+', rest[0]) and re.fullmatch('[jg]{1,24}', rest[1]):
            seconds = min(max(int(rest[0]), 3), 600)  # but past JS's largest number: test_homeart_seconds
            jpegs = [i + 1 for i, kind in enumerate(rest[1]) if kind == 'j']
            art = (seconds, jpegs) if jpegs else None
            continue
        if word == 'page':
            page = rest[0] if len(rest) == 1 and rest[0] in [p for _, p in PAGES.values()] else None
            continue
        def row(*act):
            return next((i for i, (pg, name, acts, *_) in enumerate(ROWS) if pg[1] == page and name == rest[0] and acts & {*act}), None)
        if word == 'text':  # at least a word after the row's name, all of them the row's
            i = row('CR_TEXT') if len(rest) > 1 else None
            if i is not None:
                words = studio_words(' '.join(rest[1:]))
                kept[i] = (kept[i][0] & ~TEXT | (TEXT if words else 0), *kept[i][1:6], words)
            continue
        count, act = VERBS.get(word, (None, None))
        if len(rest) != count: continue
        i = row(act, 'CR_MOVE') if word == 'size' else row(act)  # a size on a row that moves: its box
        if i is None or not all(re.fullmatch('-?[0-9]+', v) for v in rest[1:]): continue
        v = [max(-30000, min(30000, int(v))) for v in rest[1:]]
        flags, x, y, w, h, font, words = kept[i]
        _, _, acts, default, box = ROWS[i]
        if word == 'font':
            font = min(max(v[0], 8), 120) if v[0] else 0
            font = 0 if font == default else font
        elif word == 'size' and 'CR_SIZE' not in acts:
            w, h, flags = v[0], v[1], flags | SIZE
        elif word == 'size':
            least, bw, bh = 12 if 'CR_FONT' in acts else 40, box[2] - box[0], box[3] - box[1]
            if 'CR_SQUARE' in acts:
                v, bw, bh = [min(v)] * 2, min(bw, bh), min(bw, bh)
            w, h, flags = min(max(v[0], least), max(bw, least)), min(max(v[1], least), max(bh, least)), flags | SIZE
        elif word == 'move':
            x, y, flags = v[0], v[1], flags | MOVE
        else:
            flags |= HIDE
        kept[i] = flags, x, y, w, h, font, words
    return kept, art


# The parse
def test_rules_take_what_the_studios_reader_takes(fw, cr_fields):
    rng = random.Random(3)
    names = [name for _, name, *_ in ROWS] + ['img_more', 'btn_eq', 'view_eqchip', 'label_clock', 'Img_cover', 'img_cover\t',
                                              'img_cover\r', '']
    pages = ['playing_page', 'saverscreen_page', 'home_page', 'system_bar', 'Playing_page', 'playing_page ', '']
    numbers = ['0', '-0', '7', '-7', '12', '40', '186', '303', '0099', '-30000', '30001', '1' * 40, '-' + '9' * 40, '+5', '5a', '',
               '1.5', '-', '--3', '\xd9\xa3', ' 5']
    kinds = ['j', 'jgj', 'gjg', 'g', 'gg', 'j' * 24, 'jg' * 12, 'j' * 25, 'jx', 'J', 'j\r', '']
    # a row's words, as UTF-8 read byte for byte: clean ones (24 characters, 40 bytes), then unclean ones and invalid UTF-8
    words = [w.encode().decode('latin-1') for w in ('Songs', 'Now Playing', 'M\xfasica', '\u97f3\u4e50 \u5e93', 'x' * 24, '\xe9' * 20,
                                                    'Ok \U0001f3b5', 'a  b', ' a', 'a ', 'a\tb', 'a\x85b', 'a\xa0b', 'x' * 25,
                                                    '\xe9' * 20 + 'a', 'a\x7fb', 'a\u3000b', '\ufeffa', 'a\u2028b', 'a\u1680b', '\x01')]
    words += ['\xff', 'a\xe2\x80', '\xc0\xa0', '\xed\xa0\x80']

    def line():
        n = lambda: rng.choice(numbers[:9] * 3 + numbers)  # noqa: E731
        t = lambda: rng.choice(words[:7] * 3 + words)  # noqa: E731
        name = rng.choice(names)
        if rng.random() < 0.1:
            return rng.choice([f'page {rng.choice(pages[:3] * 6 + pages)}', 'page', 'page playing_page x'])
        if rng.random() < 0.08:
            return rng.choice([f'homeart {n()} {rng.choice(kinds[:7] * 3 + kinds)}'] * 4 +
                              [f'homeart {n()}', f'homeart {n()} j g', f'Homeart 5 j', f'homeart  5 j', f'homeart 5 j '])
        if rng.random() < 0.6:
            return rng.choice([f'hide {name}', f'move {name} {n()} {n()}', f'size {name} {n()} {n()}', f'font {name} {n()}',
                               f'text {name} {t()}', f'text {name} {t()} {t()}'])
        return rng.choice([f'hide {name} {n()}', f'move {name} {n()}', f'size {name} {n()} {n()} {n()}', f'font {name}',
                           f'font  {name} {n()}', f'Hide {name}', f' hide {name}', f'hide {name} ', f'move\t{name} {n()} {n()}',
                           f'text {name}', f'text {name} ', f'text  {name} {t()}', f'text\t{name} {t()}', 'accent #c04a8a', 'end'])
    ends, seen, refused, arts = ['\n', '\n', '\n', '\r\n', '\r\r\n'], set(), 0, set()
    for _ in range(6000):
        head, tail = rng.choice(['q2theme 1'] * 9 + ['q2theme 2']), rng.choice(['end'] * 9 + ['End'])
        parts = [head, f'page {rng.choice(pages[:3])}', *(line() for _ in range(rng.randrange(12))), tail]
        data = ''.join(part + rng.choice(ends) for part in parts).encode('latin-1')
        want, art = studio_rules(data)
        refused += parse(fw, data) == 0
        assert state(fw, cr_fields) == want and art_state(fw, cr_fields) == art, data
        arts.add(art and (art[0], len(art[1]) > 1))
        seen |= {(i, f) for i, (flags, *_, font, _) in enumerate(want) for f in (HIDE, MOVE, SIZE, 8, TEXT)
                 if flags & f or (f == 8 and font)}
    takes = {(i, f) for i, (_, _, acts, *_) in enumerate(ROWS) for f, a in ((HIDE, 'CR_HIDE'), (MOVE, 'CR_MOVE'), (SIZE, 'CR_SIZE'),
                                                                           (8, 'CR_FONT'), (TEXT, 'CR_TEXT')) if a in acts}
    takes |= {(i, SIZE) for i, (_, _, acts, *_) in enumerate(ROWS) if 'CR_MOVE' in acts}  # the box a row that moves is centred in
    assert seen == takes and refused > 500  # each row's each rule, and refused files, met
    assert {None, (3, True), (600, True), (12, True), (12, False)} <= arts  # and homeart, held at both ends


@pytest.mark.parametrize('seconds, want', [(b'2', 3), (b'601', 600), (b'-0', 3), (b'9' * 400, 600), (b'-' + b'9' * 400, 3)])
def test_homeart_seconds(fw, cr_fields, seconds, want):
    """Held to 3..600, a number past JS's largest (about 1.8e308) too, which the Studio's Number makes Infinity and
    cleanArt then reads as its 12."""
    parse(fw, theme_file(b'homeart ' + seconds + b' j'))
    assert art_state(fw, cr_fields) == (want, [1])


def test_a_malformed_page_line_ends_the_page(fw, cr_fields):
    parse(fw, rules(b'hide img_cover', b'page playing_page x', b'hide scrlabel_title', b'page', b'hide img_playmode',
                    b'page home_page', b'hide slider_play', b'page saverscreen_page', b'hide img_cover', b'hide img_slide'))
    hidden = [ROWS[i][1] for i, (flags, *_) in enumerate(state(fw, cr_fields)) if flags]
    assert hidden == ['img_cover', 'img_slide']  # each named on its own page; under a bad page line, none


@pytest.mark.parametrize('lines, want', [
    ((b'font scrlabel_title 30', b'font scrlabel_title 0'), 0),  # 0 clears it
    ((b'font scrlabel_title 30', b'font scrlabel_title -0'), 0),
    ((b'font scrlabel_title 30', b'font scrlabel_title 22'), 0),  # so does the row's own size
    ((b'font scrlabel_title 30', b'font scrlabel_title 3'), 8),
    ((b'font scrlabel_title 999999999999',), 120),
])
def test_a_font_is_cleared_by_0_or_its_own_size_and_held_to_8_to_120(fw, cr_fields, lines, want):
    parse(fw, rules(*lines))
    assert state(fw, cr_fields)[1][5] == want


# The gate
def hooks_silent(fw):
    """Every hook, with no rule for it: no stock call, no widget written, and coverflow.c's Home hooks shut."""
    before, win = len(fw.calls()), C.create_string_buffer(0x400)
    for page in PAGES.values():
        fw.fn('card_rules_apply', None, C.c_void_p, C.c_int)(C.addressof(win), page[0])
    assert fw.fn('card_rule_px', C.c_int, C.c_void_p, C.c_int)(C.addressof(win), 15) == 15
    assert fw.fn('card_rules_art_hidden', C.c_int)() == 0
    return fw.calls()[before:] == [] and fw.var('card_home', C.c_int).value == 0


@pytest.mark.parametrize('file, t', [(None, 'minimal'), (None, 'modern'), (EVERY, 'classic-light'), (EVERY, 'classic-modern')])
def test_no_rules_no_call(fw, card, file, t):
    if file:
        card.files[DST] = file
    start(fw, THEME=INDEX[t])
    assert hooks_silent(fw)


@pytest.mark.parametrize('t, tried', [('classic-light', None), ('classic-modern', None), ('minimal', b'2'), ('modern', 'fail')])
def test_rules_are_dropped_wherever_the_file_doesnt_apply(fw, card, t, tried):
    """Classic and Classic Modern take none of a card theme's rules, only its battery; a start the guard runs without
    the file (two failed starts, or a count it can't write) none of it. The whole state, so a field added later is
    covered."""
    card.files[DST] = EVERY
    if tried == 'fail':
        card.fail_rename = True
    elif tried:
        card.files[TRY] = tried
    start(fw, THEME=INDEX[t], BATTERY=0)
    raw, home, used = whole_state(fw)
    assert raw == bytes(len(raw)) and home == used == 0 and hooks_silent(fw)
    assert fw.fn('ipod_battery', C.c_int)() == (0 if tried else STYLES.index('inside'))


@pytest.mark.parametrize('file, used, home', [(rules(b'hide img_cover'), 1, 0),  # Now Playing's bit
                                             (rules(b'text btn_folder Albums', page=b'home_page'), 5, 1),  # Home's, its rows'
                                             (theme_file(b'homeart 5 gj'), 2, 2)])  # its art's
def test_rules_alone_are_counted_by_the_guard(fw, card, file, used, home):
    card.files[DST] = file
    start(fw, THEME=INDEX['minimal'])
    assert card.files[TRY] == b'1' and whole_state(fw)[1:] == (home, used)


# Now Playing, as tools/ipod.py lays it out on the ipod set: (parent, name, box)
PLAYING = [('win', 'view_buttons', (0, 0, 375, 40)), ('view_buttons', 'img_more', (303, 0, 50, 40)),
           ('view_buttons', 'label_ipod_pos', (22, 0, 281, 40)), ('win', 'slide_view_view', (0, 40, 375, 192)),
           ('slide_view_view', 'slide_view', (0, 0, 375, 186)),
           ('slide_view', 'view_album', (0, 0, 100, 100)),  # the asset's, laid out to 375 x 186 after the hook
           ('view_album', 'img_cover', (16, 2, 166, 166)), ('view_album', 'img_playstate', (39, 25, 120, 120)),
           ('view_album', 'scrlabel_title', (194, 18, 165, 28)), ('view_album', 'scrlabel_artist', (194, 52, 165, 22)),
           ('view_album', 'label_ipod_album', (194, 80, 165, 22)), ('view_album', 'img_playmode', (247, 133, 50, 50)),
           ('view_album', 'img_fav', (303, 133, 50, 50)), ('slide_view_view', 'slide_indicator1', (0, 182, 375, 10)),
           ('win', 'label_playtime', (46, 265, 80, 16)), ('win', 'label_ipod_remain', (249, 265, 80, 16)),
           ('win', 'slider_play', (21, 233, 333, 44)), ('win', 'image_wait', (72, 98, 54, 54))]


class Page:
    """Now Playing's window as stock's init leaves it on the ipod set (Now's queue, times and init), a heart whose
    click is stock's My Fav toggle, and the theme t with a card theme of lines."""
    def __init__(self, fw, ui, mcl, st, card, *lines, t=MINIMAL, **keys):
        card.files[DST] = rules(*lines)
        theme(fw, st, t, **keys)
        Now(fw, ui, mcl, st, opened=False)
        self.fw, self.ui = fw, ui
        self.win = ui.top = ui.widget(ui.wm, 'playing_page', 0, 0, 375, 320, kind='window')
        self.w = {'win': self.win}
        for parent, name, box in PLAYING:
            self.w[name] = ui.widget(self.w[parent], name, *box)
        self.emitter, self.item = C.create_string_buffer(0x40), C.create_string_buffer(0x40)
        struct.pack_into('<Q', self.emitter, 0, C.addressof(self.item))
        struct.pack_into('<Q', self.item, 0, 0x5eed)  # emitter_on_with_tag's item: {ctx, id, type, handler...}
        struct.pack_into('<i', self.item, EMIT_TYPE, EVT_CLICK)
        struct.pack_into('<Q', self.item, 12, FAV_CLICK)
        C.c_void_p.from_address(self.w['img_fav'] + W_EMITTER).value = C.addressof(self.emitter)
        assert fw.fn('ringnav_playing', C.c_int, C.c_void_p, C.c_void_p)(self.win, 0x77) == 41

    def box(self, name):
        return self.ui.box(self.w[name])

    def calls(self, stub, name):
        """stub's value arguments for widget name, in order."""
        return [args[1] for _, args in self.fw.calls(stub) if args[0] == self.w[name]]


@pytest.fixture
def page(fw, ui, mcl, st, card):
    return lambda *lines, **keys: Page(fw, ui, mcl, st, card, *lines, **keys)


@pytest.mark.parametrize('name', ['img_cover', 'scrlabel_title', 'label_ipod_pos', 'label_playtime', 'slider_play', 'slide_indicator1'])
def test_np_hide_is_visibility_opacity_and_sensitivity(fw, page, name):
    p = page(b'hide ' + name.encode())
    assert p.ui.shown[p.w[name]] == 0 and p.calls('widget_set_opacity', name) == [0]
    assert p.calls('widget_set_sensitive', name) == [0]


@pytest.mark.parametrize('t', [MINIMAL, MODERN], ids=['minimal', 'modern'])
@pytest.mark.parametrize('line, name, want', [
    (b'move img_fav 9999 -50', 'img_fav', (325, 0, 50, 50)),  # to the 375 x 186 box's far edge, not the 100 x 100 parent's
    (b'move scrlabel_title -3 170', 'scrlabel_title', (0, 158, 165, 28)),
    (b'move label_ipod_pos 400 7', 'label_ipod_pos', (22, 0, 281, 40)),  # it ends at More's x: no room to move across
    (b'move img_playmode 10 20', 'img_playmode', (10, 20, 50, 50)),
])
def test_a_move_stays_in_the_parents_laid_out_box(page, t, line, name, want):
    assert page(line, t=t).box(name) == want


def test_a_size_alone_keeps_the_widget_in_its_box(page):
    p = page(b'size scrlabel_title 300 9', b'size label_ipod_album 400 400')
    assert p.box('scrlabel_title') == (75, 18, 300, 12)  # 12 high at least; pushed left to hold 300 in 375
    assert p.box('label_ipod_album') == (0, 0, 375, 186)


def test_a_size_it_cant_take_centres_it_in_the_studios_box(page):
    """The Studio moves a row it sizes to fit that size: one that keeps its own sits centred where it drew it."""
    p = page(b'move img_playmode 247 106', b'size img_playmode 80 80', b'size img_fav 80 80')
    assert p.box('img_playmode') == (262, 121, 50, 50)
    assert p.box('img_fav') == (318, 136, 50, 50)  # centred on its own place's 80 x 80, held to the 186 high box


def test_the_lock_song_card_sized_by_the_studio_stays_centred(fw, card):
    win = lock(fw, card, b'move view_playinfo 0 190', b'size view_playinfo 375 80')
    assert [args[:4] for name, args in win.calls('widget_move_resize') if name == 'view_playinfo'][-1] == (20, 190, 335, 80)


def test_the_art_stays_square_and_takes_its_glyph_and_spinner(page):
    p = page(b'size img_cover 100 160', b'move img_cover 200 90')
    assert p.box('img_cover') == (200, 86, 100, 100)  # the smaller side; held in the box
    assert p.box('img_playstate') == (190, 76, 120, 120)  # centred on it, in its parent
    assert p.ui.screen(p.w['image_wait']) == (223, 149, 54, 54)  # the spinner too, a child of the window


def test_a_hidden_art_hides_its_glyph_through_np_theme(fw, page):
    """Modern shows the play-state glyph (np_state_op 255): over a hidden art it would be an orphan, and the art's tap."""
    p = page(b'hide img_cover', t=MODERN)
    assert MODERN['np_state_op'] == 255 and p.calls('widget_set_opacity', 'img_playstate') == [0, 0]  # the rule's, np_theme's
    assert p.ui.shown[p.w['img_playstate']] == 0 and p.calls('widget_set_sensitive', 'img_playstate') == [0]
    fw.fn('np_theme', None)()  # a Theme change: still clear
    assert p.calls('widget_set_opacity', 'img_playstate')[-1] == 0


def test_the_heart_moves_before_its_fill(page):
    p = page(b'move img_fav 20 100')
    fill = [w for w in p.ui.kids[p.w['view_album']] if not p.ui.names[w]]
    assert p.box('img_fav') == (20, 100, 50, 50) and [p.ui.box(f) for f in fill] == [(20, 100, 50, 50)]


@pytest.mark.parametrize('t', [MINIMAL, MODERN], ids=['minimal', 'modern'])
def test_centred_now_playing_takes_none(fw, page, t):
    p = page(b'hide img_cover', b'move img_fav 0 0', b'font scrlabel_title 40', t=t, NPCENTRED=1)
    assert p.box('img_fav') == (303, 133, 50, 50) and p.ui.shown.get(p.w['img_cover'], 1) == 1
    assert p.calls('widget_set_sensitive', 'img_cover') == [] and p.calls('widget_set_opacity', 'img_cover') == []
    assert p.calls('widget_set_opacity', 'img_playstate') == [t['np_state_op']]  # Centred's glyph, its art showing
    assert (p.w['scrlabel_title'], b'_card_px') not in p.ui.ints


def test_font_is_held_to_the_labels_height(page):
    p = page(b'font scrlabel_title 60', b'font scrlabel_artist 9', b'font label_playtime 120')
    px = {name: {k: p.ui.ints.get((p.w[name], k)) for k in (b'style:normal:font_size', b'style:focused:font_size',
                                                              b'style:disable:font_size', b'_card_px')}
          for name in ('scrlabel_title', 'scrlabel_artist', 'label_playtime')}
    assert px == {'scrlabel_title': dict.fromkeys(px['scrlabel_title'], 28), 'scrlabel_artist': dict.fromkeys(px['scrlabel_artist'], 9),
                  'label_playtime': dict.fromkeys(px['label_playtime'], 16)}


class Canvas:
    """paint_caps' canvas: each capital drawn as (x, y, px)."""
    def __init__(self, fw):
        self.lcd, self.raw, self.px, self.drawn = C.create_string_buffer(0x200), C.create_string_buffer(0x100), 0, []
        struct.pack_into('<Q', self.raw, O['CANVAS_LCD'], C.addressof(self.lcd))
        fw.mock('canvas_get_clip_rect', lambda c, r, *_: C.memmove(r, struct.pack('<4i', 0, 0, 375, 320), 16) and 0)
        fw.mock('canvas_set_font', lambda c, face, px, *_: setattr(self, 'px', px))
        fw.mock('canvas_measure_text', lambda *_: 10.0)
        fw.mock('canvas_draw_text', lambda c, s, n, x, y, *_: self.drawn.append((C.c_int(x).value, C.c_int(y).value, self.px)))


@pytest.mark.parametrize('lines, want', [((), (80 + (22 - 15) // 2, 15)), ((b'font label_ipod_album 20',), (80 + 1, 20)),
                                         ((b'font label_ipod_album 40',), (80, 22)), ((b'hide label_ipod_album',), None)])
def test_minimal_capitals_follow_the_albums_rules(fw, page, lines, want):
    p = page(*lines)
    p.ui.set_text(p.w['label_ipod_album'], 'Blue Train')
    canvas = Canvas(fw)
    fw.fn('navigation__paint_caps', None, C.c_void_p, C.c_void_p)(p.w['view_album'], C.addressof(canvas.raw))
    assert {(y, px) for _, y, px in canvas.drawn} == ({want} if want else set())


# The lock screen
LOCK = [('img_screensaver', (0, 0, 375, 320)), ('view_timeinfo', (0, 65, 375, 103)), ('clock_time', (72, 0, 151, 50)),
        ('clock_date', (72, 60, 205, 39)), ('label_battery', (232, 0, 42, 24)), ('img_battery', (235, 24, 34, 20)),
        ('view_playinfo', (20, 190, 335, 80)), ('scrlabel_title', (80, 14, 245, 28)), ('scrlabel_artist', (80, 42, 220, 20)),
        ('img_slide', (172, 285, 30, 16))]


def lock(fw, card, *lines, t='minimal', **config):
    card.files[DST] = rules(*lines, page=b'saverscreen_page')
    start(fw, THEME=INDEX[t], **config)
    LockBitmaps(fw)
    fw.var('fw_ctx_g_screensaver_picture', C.c_char * 256).value = b'screensaver_default'
    fw.mock('stock_saver_trampoline', lambda *_: 31)
    win = Window(fw, [])
    for name, (x, y, w, h) in LOCK:
        struct.pack_into('<ii', (C.c_char * 8).from_address(win.add(name, w, h) + O['W_X']), 0, x, y)
    assert fw.fn('lockscreen_init', C.c_int, C.c_void_p, C.c_void_p)(0x7700, 0x7800) == 31
    return win


@pytest.mark.parametrize('t', ['minimal', 'modern'])
def test_lock_rules_run_after_big_clock(fw, card, t):
    win = lock(fw, card, b'hide label_battery', b'move view_timeinfo 0 400', b'move img_slide -9 0', b'hide view_playinfo',
               t=t, LOCKCLOCK=1, BATTERY=1)
    visible = [(name, args[0]) for name, args in win.calls('widget_set_visible')]
    assert ('label_battery', 1) in visible and visible[-2:] == [('label_battery', 0), ('view_playinfo', 0)]  # big_clock's, then the rule
    moves = [(name, args[:4]) for name, args in win.calls('widget_move_resize')]
    assert moves[-2:] == [('view_timeinfo', (0, 320 - 103, 375, 103)), ('img_slide', (0, 0, 30, 16))]
    assert ('view_timeinfo', (0, 14, 375, 150)) in moves[:-2]  # Large's place first


@pytest.mark.parametrize('t', ['minimal', 'modern'])
def test_at_a_glance_takes_no_lock_rules(fw, card, t):
    """At a Glance is every theme's alike: a card theme's lock rules are Default's alone."""
    win = lock(fw, card, b'move img_slide -9 0', b'move view_playinfo 0 9', b'hide img_screensaver', t=t, LOCKLAYOUT=1)
    moves = [(name, args[:4]) for name, args in win.calls('widget_move_resize')]
    assert ('img_slide', (0, 0, 30, 16)) not in moves and moves[-1] != ('view_playinfo', (0, 9, 335, 80))
    assert ('view_playinfo', (0, 76, 375, 150)) in moves
    assert ('img_screensaver', 0) not in [(name, args[0]) for name, args in win.calls('widget_set_visible')]


# Home
HOME_ROWS = [name for pg, name, *_ in ROWS if pg[1] == 'home_page']
SIZES = ('style:normal:font_size', 'style:focused:font_size', 'style:disable:font_size', '_card_px')
ROCKBOX = b'/mnt/mmc/.rockbox/rockbox'
NOT_HOME = theme_file(b'page playing_page', b'hide img_cover', b'font scrlabel_title 30', b'page saverscreen_page',
                      b'hide clock_time')


def arguments(stub, regs):
    """A stub's arguments out of its six registers (the rest hold whatever the caller left), but a void * (stat's
    buffer, on the caller's stack, which ASan moves each call)."""
    args = {f'fw_{k}': v for k, v in host.tools_peq.LIBC.items()}.get(stub) or host.tools_build.FUNCTIONS.get(stub, ('', ''))
    return tuple(r for r, t in zip(regs, args[1].split(', ')) if t not in ('', 'void', 'void *'))


class HomePage:
    """Home's window as tools/ipod.py lays it out on the ipod set, opened by coverflow_home under theme t with a card
    theme of Home lines: the art and the main list, each of its rows (HOME_ROWS) a view holding its label, which reads
    the row's name, and its tap image. rockbox: the card has Rockbox (Shortcut: Rockbox shows the Shortcut row)."""
    def __init__(self, fw, card, *lines, t='minimal', **config):
        card.files[DST] = rules(*lines, page=b'home_page')
        self.fw, self.card, self.rockbox = fw, card, False
        fw.mock('fw___xstat', self.stat)
        start(fw, THEME=INDEX[t], **config)
        self.ui = ui = HomeUi(fw)
        self.win = ui.make(0, 'window', 0, 30, 375, 290)
        ui.make(self.win, 'image', 0, 0, 375, 290, name='img_homeart')
        self.view = ui.make(ui.make(self.win, 'list_view', 0, 0, 188, 273, name='list_view_home'), 'scroll_view', 0, 0, 188, 273)
        self.rows, self.labels = {}, {}
        for i, name in enumerate(HOME_ROWS):
            self.rows[name] = ui.make(self.view, 'view', 0, 39 * i, 188, 39, name=name)
            self.labels[name] = ui.make(self.rows[name], 'hscroll_label', 20, 0, 150, 39, name='label_' + name[4:])
            ui[self.labels[name]].text = name
            ui.make(self.rows[name], 'image', 0, 0, 188, 39, name='img_' + name[4:])
        ui.top = self.win
        fw.mock('stock_home_trampoline', lambda *_: 0)
        fw.fn('coverflow_home', C.c_int, C.c_void_p, C.c_void_p)(self.win, 0)

    def stat(self, version, path, info, *_):
        if self.rockbox and C.string_at(path) == ROCKBOX:
            (C.c_uint * 36).from_address(info)[5] = 0o100000  # a regular file
            return 0
        return self.card.stat(version, path, info)

    def order(self):
        return [self.ui[w].name for w in self.ui[self.view].kids]

    def text(self, name):
        return self.ui[self.labels[name]].text

    def fonts(self, name):
        return {k: v for k, v in self.ui[self.labels[name]].props.items() if k in SIZES}


@pytest.fixture
def home(fw, card):
    return lambda *lines, **keys: HomePage(fw, card, *lines, **keys)


@pytest.mark.parametrize('t', ['minimal', 'modern'])
def test_hidden_rows_go_last_in_order_and_take_no_taps(fw, home, t):
    """A rule's hides and Home's own (the Shortcut row with no Rockbox on the card, Hide Folder) go last, in HOME_ROWS'
    order, the rest in it; that holds at the next layout, Rockbox there now. Today's restack alone puts the Shortcut row
    at its fixed index, among rows a rule sent last."""
    h = home(b'hide btn_playing', b'hide btn_stream', t=t, SHORTCUT=0, HIDEFOLDER=1)
    hidden = ['btn_playing', 'btn_folder', 'btn_rockbox', 'btn_stream']
    assert h.order() == ['btn_localmusic', 'btn_coverflow', 'btn_settings', *hidden]
    for name, row in h.rows.items():
        assert h.ui[row].visible == h.ui[row].sensitive == (name not in hidden), name
    assert {w for w, v, *_ in (a for _, a in fw.calls('widget_set_opacity')) if not v} >= {h.rows['btn_playing'], h.rows['btn_stream']}
    h.rockbox = True
    fw.fn('coverflow_home_layout', None)()
    assert h.order() == ['btn_localmusic', 'btn_coverflow', 'btn_rockbox', 'btn_settings', 'btn_playing', 'btn_folder', 'btn_stream']
    assert h.ui[h.rows['btn_rockbox']].visible and h.ui[h.rows['btn_rockbox']].sensitive


def test_home_words_come_before_home_width(fw, home):
    """A row's words are on its label before home_width lays the label out, so theme_font picks its face for them."""
    h = home(b'text btn_playing Songs', b'text btn_folder Albums')
    label = h.labels['btn_playing']
    calls = [(name, args[0]) for name, args in fw.calls() if name in ('widget_set_text_utf8', 'widget_move_resize')]
    assert h.text('btn_playing') == 'Songs' and h.text('btn_folder') == 'Albums' and h.text('btn_coverflow') == 'btn_coverflow'
    assert calls.index(('widget_set_text_utf8', label)) < calls.index(('widget_move_resize', label))


def test_settings_takes_words_never_a_hide(fw, home, cr_fields):
    """Settings is always on Home: its hide is no rule, its words and font are."""
    h = home(b'hide btn_settings', b'text btn_settings Setup', b'font btn_settings 24')
    row = h.rows['btn_settings']
    assert h.ui[row].visible and h.ui[row].sensitive and h.text('btn_settings') == 'Setup'
    assert h.fonts('btn_settings') == dict.fromkeys(SIZES, 24) and state(fw, cr_fields)[ROWS.index(ROWS[-1])][0] == TEXT


def test_the_shortcut_row_takes_no_words(fw, home, cr_fields):
    """Its label is the Shortcut's, written at each layout: no words, but its font and hide."""
    h = home(b'text btn_rockbox Mine', b'font btn_rockbox 24', SHORTCUT=6)  # Games
    assert h.text('btn_rockbox') == 'Games' and h.fonts('btn_rockbox') == dict.fromkeys(SIZES, 24)
    assert state(fw, cr_fields)[HOME + HOME_ROWS.index('btn_rockbox')][0] == 0


@pytest.mark.parametrize('words, taken', [
    (b'Songs', True), (b'Now Playing', True), ('音乐 \U0001f3b5'.encode(), True), (b'x' * 24, True),
    ('\xe9'.encode() * 20, True),  # 40 bytes
    (b'x' * 25, False),  # a 25th character
    ('\xe9'.encode() * 20 + b'a', False),  # a 41st byte
    (b'Now  Playing', False), (b' Songs', False), (b'Songs ', False), (b'So\tngs', False), (b'So\x7fngs', False),
    (b'So\xc2\x85ngs', False),  # a C1 control
    (b'Now\xc2\xa0Playing', False), ('Now　Playing'.encode(), False),  # spaces the Studio writes as ' '
    (b'So\xffngs', False), (b'Songs\xe2\x80', False), (b'\xc0\xa0', False),  # not UTF-8
])
def test_text_is_taken_only_clean(home, words, taken):
    h = home(b'text btn_playing ' + words)
    assert h.text('btn_playing') == (words.decode() if taken else 'btn_playing')


@pytest.mark.parametrize('t', ['minimal', 'modern'])
def test_home_fonts_are_held_to_the_rows_height(home, t):
    h = home(b'font btn_playing 60', b'font btn_folder 24', b'font btn_coverflow 18', t=t)  # 18: the row's own, none
    assert h.fonts('btn_playing') == dict.fromkeys(SIZES, 39) and h.fonts('btn_folder') == dict.fromkeys(SIZES, 24)
    assert h.fonts('btn_coverflow') == {}


@pytest.mark.parametrize('lines, px', [((), 18), ((b'font btn_folder 24',), 24), ((b'font btn_folder 60',), 39)])
def test_minimal_home_capitals_take_the_rows_font(fw, home, lines, px):
    h = home(*lines)
    canvas = Canvas(fw)
    fw.fn('navigation__paint_caps', None, C.c_void_p, C.c_void_p)(h.labels['btn_folder'], C.addressof(canvas.raw))
    assert canvas.drawn and {(y, size) for _, y, size in canvas.drawn} == {((39 - px) // 2, px)}


@pytest.mark.parametrize('t, file', [('minimal', NOT_HOME), ('modern', NOT_HOME), ('classic-light', EVERY), ('classic-modern', EVERY)])
def test_no_home_rule_lays_home_out_as_with_no_rule(fw, home, card, t, file):
    """Home's layout with no rule for it (rules for other pages, or a file of every kind under Classic and Classic
    Modern, which drop it) makes the stock calls it makes with no rule at all, which look up only the rows it lays out
    itself: card_home shuts coverflow.c's hook."""
    home(t=t)  # a file with no rule

    def layout():
        fw.var('fw_log_n', C.c_int).value = 0  # the log of stub calls holds 8192
        fw.fn('coverflow_home_layout', None)()
        assert len(fw.calls()) < 8192
        return [(name, arguments(name, a)) for name, a in fw.calls() if name != 'timer_remove']  # the last timer's id
    alone = layout()
    assert {C.string_at(a[1]) for name, a in alone if name == 'widget_lookup'} == {b'btn_playing', b'btn_folder', b'btn_rockbox',
                                                                                     b'btn_stream', b'label_rockbox'}
    card.files[DST] = file
    fw.fn('card_theme_load', None)()
    assert fw.var('card_home', C.c_int).value == 0 and layout() == alone


# Home's art
PICTURE = '/mnt/mmc/Q2Pod/images/art%02d.%s'
PNG = b'\x89PNG\r\n\x1a\n\0\0\0\rIHDR' + struct.pack('>II', 64, 64) + bytes(9)


def picture(w=64, h=64, size=0, marker=0xc0):
    data = jpeg(w, h, marker)
    return data + bytes(max(0, size - len(data)))


class Art:
    """Home in Split under homeart, the card's pictures (art number: bytes, which a decode answers with a 64 px
    picture), the clock at a turn's start; paint() paints Home and answers the art it shows, its number (0: none): the
    last one loaded, under its key. events: the card's art files opened, and their loads and unloads by name, as they
    happen (the names are a stack buffer's, or one the next turn writes)."""
    def __init__(self, fw, home, line, pictures, t='minimal'):
        self.fw, self.h, self.events = fw, home(line, t=t), []
        for n, data in pictures.items():
            self.h.card.files[PICTURE % (n, 'jpg')] = data
        self.clock = Clock(fw, 0)
        self.bitmaps = Bitmaps(fw, {'file://' + PICTURE % (n, 'jpg'): (64, 64, (C.c_uint * 4096)()) for n in pictures})
        self.home = fw.var('coverflow__home', Home)
        fw.mock('fw_fopen', lambda path, *rest: self.note('open', path) or self.h.card.fopen(path, *rest))
        fw.mock('widget_load_image', lambda w, url, *rest: self.note('load', url) or self.bitmaps.load(w, url, *rest))
        fw.mock('image_manager_unload_bitmap_by_name', lambda m, name, *_: self.note('unload', name) or 0)

    def note(self, what, name):
        name = C.string_at(name).decode()
        if 'Q2Pod/images' in name:
            self.events.append((what, name[-9:]))

    def paint(self, at=None):
        if at is not None:
            self.clock.now = at
        self.fw.fn('coverflow_home_art', None, C.c_void_p)(self.h.win)
        loaded = [url for url in self.bitmaps.loads if 'Q2Pod/images' in url]
        n = int(loaded[-1][-6:-4]) if loaded else 0
        return n if n and self.home.key == 1 << 31 | n else 0

    def reads(self):
        return [name for what, name in self.events if what == 'open']


@pytest.fixture
def art(fw, home):
    return lambda line, pictures, **keys: Art(fw, home, line, pictures, **keys)


def test_homeart_keeps_positional_numbers_over_jpegs(fw, art, cr_fields):
    """Each j keeps its number among all the letters; a g is never opened."""
    a = art(b'homeart 5 jgjg', {1: picture(), 2: picture(), 3: picture()})
    assert art_state(fw, cr_fields) == (5, [1, 3])
    assert [a.paint(t) for t in (0, 4999, 5000, 10000, 15000)] == [1, 1, 3, 1, 3]
    assert set(a.reads()) == {'art01.jpg', 'art03.jpg'}


@pytest.mark.parametrize('data, shown', [
    (picture(1024, 1024, 1 << 20), True),  # at both caps
    (picture(1024, 1024, 1 << 20, marker=0xc2), True),  # progressive
    (picture(1025, 64), False), (picture(64, 1025), False),
    (picture(size=(1 << 20) + 1), False),  # over 1 MB
    (picture(marker=0xc4), False),  # no frame header: a JPEG it can't size
    (picture(0, 64), False),  # a frame with no width
    (PNG, False), (b'GIF89a' + bytes(40), False), (None, False),  # not a JPEG, or no file
], ids=['caps', 'progressive', 'wide', 'tall', 'heavy', 'unsized', 'empty', 'png', 'gif', 'none'])
def test_art_refuses_what_it_cant_size_or_holds_too_big(fw, art, data, shown):
    """A refused turn shows what Home shows with no homeart: the art is never decoded."""
    a = art(b'homeart 5 j', {} if data is None else {1: data})
    assert a.paint(0) == shown
    if not shown:
        loads = a.bitmaps.loads[:]
        fw.var('card_home', C.c_int).value, a.home.key = 0, 0
        a.bitmaps.loads.clear()
        a.paint()
        assert loads == a.bitmaps.loads


def test_art_turns_unload_the_last(fw, art):
    """At most one of the card's pictures is held: a turn that shows another, or none (refused), unloads the last by
    name; one picture over turns stays loaded."""
    def held():
        names = set()
        for what, name in a.events:
            if what == 'load':
                names.add(name)
            elif what == 'unload':
                names.discard(name)
        return names
    a = art(b'homeart 3 jjj', {1: picture(), 2: picture(), 3: picture(2000)})
    for t, shown in ((0, 1), (1000, 1), (3000, 2), (6000, 0), (9000, 1), (12000, 2)):
        assert a.paint(t) == shown and held() == ({f'art{shown:02}.jpg'} if shown else set()), t


def test_one_picture_over_turns_stays_loaded(fw, art):
    a = art(b'homeart 3 j', {1: picture()})
    assert [a.paint(t) for t in (0, 3000, 6000)] == [1, 1, 1]
    assert [what for what, _ in a.events if what != 'open'] == ['unload', 'load']  # coverflow_home_art's, before its load


@pytest.mark.parametrize('data', [picture(), picture(2000)], ids=['kept', 'refused'])
def test_art_reads_the_card_once_a_turn(fw, art, data):
    """Home paints at least once a second; its picture is sized once a turn, kept or refused."""
    a = art(b'homeart 10 j', {1: data})
    a.paint(0)
    once = len(a.reads())
    for t in range(500, 10000, 500):
        a.paint(t)
    assert once and len(a.reads()) == once
    a.paint(10000)
    assert len(a.reads()) == 2 * once


@pytest.mark.parametrize('t, file', [('minimal', rules(b'hide btn_playing', page=b'home_page')), ('modern', NOT_HOME),
                                     ('classic-light', EVERY), ('classic-modern', EVERY)])
def test_no_homeart_paints_home_as_with_none(fw, home, card, t, file):
    """Home's paint with no homeart applying (Home's rows alone, rules for other pages, or every kind of line under
    Classic and Classic Modern, which drop them) makes the stock calls it makes with no file: card_home shuts the
    hook."""
    h = home(t=t)
    Bitmaps(fw)

    def paint():
        fw.var('coverflow__home', Home).key, fw.var('fw_log_n', C.c_int).value = 0, 0
        fw.fn('coverflow_home_art', None, C.c_void_p)(h.win)
        return [(name, arguments(name, a)) for name, a in fw.calls() if name != 'timer_remove']  # the last timer's id
    alone = paint()
    card.files[DST] = file
    fw.fn('card_theme_load', None)()
    assert not fw.var('card_home', C.c_int).value & 2 and paint() == alone and alone
