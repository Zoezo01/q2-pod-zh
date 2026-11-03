"""tools/theme.py: the Q2 theme XML reader, the schema and the generated theme table."""
import xml.etree.ElementTree as ET

import pytest

import theme
from theme import ThemeError


def error(f, *args):
    with pytest.raises(ThemeError) as e:
        f(*args)
    return str(e.value)


VALID = ['<!-- c -->\n<theme format="1">\n\t<view name="a&amp;&lt;&gt;&quot;"/>\r\n</theme>\n',
         '<theme format="1" id="t">\n<view name="list"><element name="row"/></view>\n</theme>']


def test_tokenize():
    assert theme.tokenize(VALID[0], 't.xml') == [('open', 'theme', {'format': '1'}, 2),
                                                 ('empty', 'view', {'name': 'a&<>"'}, 3),
                                                 ('close', 'theme', {}, 4)]
    assert theme.tokenize('<a b="&amp;lt;"/>', 't.xml') == [('empty', 'a', {'b': '&lt;'}, 1)]


@pytest.mark.parametrize('text, message', [
    ('<a/>x', 't.xml:1: text between tags'),
    ('<a/>\n\xa0', 't.xml:2: text between tags'),
    ('<![CDATA[x]]>', 't.xml:1: malformed tag'),
    ('<!DOCTYPE a>', 't.xml:1: malformed tag'),
    ('<?xml version="1.0"?>', 't.xml:1: malformed tag'),
    ("<a b='1'/>", 't.xml:1: malformed tag'),
    ('<a B="1"/>', 't.xml:1: malformed tag'),
    ('<a x:y="1"/>', 't.xml:1: malformed tag'),
    ('<a b="<"/>', 't.xml:1: malformed tag'),
    ('</a b="1">', 't.xml:1: malformed tag'),
    ('</a/>', 't.xml:1: malformed tag'),
    ('<a\n b="1" b="2"/>', 't.xml:1: duplicate attribute b'),
    ('<a b="&apos;"/>', 't.xml:1: unknown entity'),
    ('<a b="&"/>', 't.xml:1: unknown entity'),
    ('<a/>\n<!-- x', 't.xml:2: unclosed comment'),
    ('<a b="x\ty"/>', 't.xml:1: malformed tag'),
    ('<a b="x\ny"/>', 't.xml:1: malformed tag'),
    ('<a b="x\x01y"/>', 't.xml:1: malformed tag'),
    ('<!-- a -- b -->', 't.xml:1: unclosed comment'),
])
def test_tokenize_rejects(text, message):
    assert error(theme.tokenize, text, 't.xml') == message


def test_parse():
    root = theme.parse(VALID[1], 't.xml')
    assert (root.tag, root.attrs, root.line, root.file) == ('theme', {'format': '1', 'id': 't'}, 1, 't.xml')
    view, = root.children
    assert (view.tag, view.attrs, view.line) == ('view', {'name': 'list'}, 2)
    assert [(c.tag, c.attrs, c.children, c.line) for c in view.children] == [('element', {'name': 'row'}, [], 2)]


def test_tokenizer_matches_etree():
    """The format is an XML subset: every accepted document reads the same through xml.etree."""
    def mine(n): return (n.tag, n.attrs, [mine(c) for c in n.children])
    def etree(e): return (e.tag, e.attrib, [etree(c) for c in e])
    texts = VALID + [p.read_text() for p in sorted((theme.ROOT/'assets/themes').glob('*/theme.xml'))]
    for text in texts:
        assert mine(theme.parse(text, 't.xml')) == etree(ET.fromstring(text))


@pytest.mark.parametrize('text, message', [
    ('', 't.xml:1: root must be <theme format="1">'),
    ('<view/>', 't.xml:1: root must be <theme format="1">'),
    ('<theme format="2"/>', 't.xml:1: root must be <theme format="1">'),
    ('<theme format="1"/>\n<theme format="1"/>', 't.xml:2: more than one root element'),
    ('<theme format="1">\n</view>', 't.xml:2: mismatched </view>'),
    ('</theme>', 't.xml:1: mismatched </theme>'),
    ('<theme format="1">\n<view>', 't.xml:2: unclosed <view>'),
])
def test_parse_rejects(text, message):
    assert error(theme.parse, text, 't.xml') == message


def test_load(tmp_path):
    path = tmp_path/'t'/'theme.xml'
    path.parent.mkdir()
    path.write_bytes('<theme format="1" name="Ünï"/>'.encode())
    assert theme.load(path).attrs == {'format': '1', 'name': 'Ünï'}
    assert theme.load(path).file == 't/theme.xml'
    path.write_bytes(b'<theme format="1"/>' + b' ' * (32768 - 19))
    assert theme.load(path).tag == 'theme'
    path.write_bytes(b'<theme format="1"/>' + b' ' * (32769 - 19))
    assert error(theme.load, path) == 't/theme.xml:1: larger than 32 KB'
    path.write_bytes(b'<theme format="1" name="\xff"/>')
    assert error(theme.load, path) == 't/theme.xml:1: not UTF-8'


THEMES = theme.ROOT/'assets/themes'
ZERO = dict.fromkeys(('card', 'raw_red', 'fill', 'chevron', 'muted', 'bar_light'), 0)
MINIMAL_ROLES = {**ZERO, 'fill': 0xeeeeec, 'chevron': 0x777777, 'muted': 0x8c8c8c}
CLASSIC_ROLES = {**ZERO, 'bar_light': 0xdedede}


def opt(setting, default=-1, preset=-1, keep=-1, values=0):
    return {'setting': setting, 'def': default, 'preset': preset, 'keep': keep, 'values': values}


MINIMAL = {
    'id': 'minimal', 'index': 0, 'name': 'Minimal', 'accents': 0x1ff, 'accent_key': 'MINIMAL_ACCENT',
    'painter[PAINT_SELECTION]': 0, 'chevron_home': 0, 'caps_mark': 1, 'caps_home': 1, 'caps_np': 1,
    'icons_mono': 1, 'icons_badge_muted': 1, 'icons_addon_mono': 1, 'bold': 0, 'np_album_op': 0, 'np_state_op': 0,
    'home_top': 8, 'home_full_row': 375, 'home_text_x': 39, 'home_label_end[0]': 8, 'home_label_end[1]': 33,
    'set_top': 8, 'np_bar': 4, 'np_scrub': 8, 'art_square': 0, 'batt_fill': 0,
    'schemes': [{**MINIMAL_ROLES, 'raw_red': int(v == 1), 'card': int(v == 7)} for v in range(9)],
    'opts': [opt('SETTING_LIGHT', preset=0, keep=2), opt('SETTING_ACCENT', default=7, preset=0),
             opt('SETTING_ROUNDED', preset=1), opt('SETTING_HOME', preset=0), opt('SETTING_SHORTCUT'),
             opt('SETTING_BATTERY'), opt('SETTING_TIMETITLE', preset=1), opt('SETTING_NPCENTRED')]}
CLASSIC = {
    **MINIMAL, 'id': 'classic', 'index': 1, 'name': 'Classic', 'accents': 0x17f, 'accent_key': 'ACCENT',
    'painter[PAINT_SELECTION]': 1, 'chevron_home': 1, 'caps_mark': 0, 'caps_home': 0, 'caps_np': 0,
    'icons_mono': 0, 'icons_badge_muted': 0, 'icons_addon_mono': 0, 'bold': 1, 'np_album_op': 255, 'np_state_op': 255,
    'home_top': 0, 'home_full_row': 369, 'home_text_x': 33, 'home_label_end[0]': 33, 'home_label_end[1]': 33,
    'set_top': 0, 'np_bar': 8, 'np_scrub': 8, 'art_square': 1, 'batt_fill': 0x34c759,
    'schemes': [ZERO if v == 7 else {**CLASSIC_ROLES, 'raw_red': int(v == 1)} for v in range(9)],
    'opts': [opt('SETTING_LIGHT', preset=1, keep=2), opt('SETTING_ACCENT', default=0, preset=4, values=0x17f),
             opt('SETTING_ROUNDED', preset=0), opt('SETTING_HOME', preset=0), opt('SETTING_SHORTCUT'),
             opt('SETTING_BATTERY'), opt('SETTING_TIMETITLE', preset=0), opt('SETTING_NPCENTRED')]}


def bare(t):
    return {k: v for k, v in t.items() if k != 'line'}


def resolved(body, attrs='id="t" name="T" index="2" extends="minimal"'):
    by_id = {n.attrs['id']: n for n in map(theme.load, sorted(THEMES.glob('*/theme.xml')))}
    root = theme.parse(f'<theme format="1" {attrs}>{body}</theme>', 't.xml')
    return theme.resolve(root, {**by_id, root.attrs.get('id', ''): root})


def test_accent_names():
    assert theme.ACCENT_NAMES == ['graphite', 'crimson', 'tidal', 'champagne', 'cobalt', 'tangerine', 'plum', 'ivory', 'green']
    assert theme.ACCENT_NAMES[theme.O['CRIMSON']] == 'crimson'
    assert theme.ACCENT_NAMES[theme.O['MINIMAL_ACCENT']] == 'ivory'


def test_frozen_table():
    """Today's Minimal and Classic, as the hand-written table had them."""
    assert [bare(t) for t in theme.builtins()] == [MINIMAL, CLASSIC]


def test_extends():
    assert bare(resolved('', 'id="t" name="T" index="2" extends="classic"')) == {**CLASSIC, 'id': 't', 'name': 'T', 'index': 2}
    assert resolved('<option setting="accent" key="ACCENT"/>', 'id="t" name="T" index="2" extends="classic"')['opts'] == [opt('SETTING_ACCENT', values=0x17f)]
    t = resolved('<colorSchemes accents="cobalt green"/><option setting="accent" key="ACCENT" default="cobalt"/>',
                 'id="t" name="T" index="2" extends="classic"')
    assert (t['accents'], t['schemes'][0], t['schemes'][4], t['opts']) == (0x110, ZERO, CLASSIC_ROLES, [opt('SETTING_ACCENT', default=4, values=0x110)])
    assert resolved('<view name="home"><element name="rows" top="12"/></view>')['home_top'] == 12  # a card theme states numbers
    assert resolved('<colorScheme name="graphite" fill="#112233"/>')['schemes'][0]['fill'] == 0x112233
    assert resolved('<view name="playing"><element name="album" opacity="255"/></view>')['np_album_op'] == 255


M = 'id="minimal" name="M" index="0"'
@pytest.mark.parametrize('body, attrs, message', [
    ('<font name="x"/>', None, 'unknown element <font>'),
    ('<view name="list"><element name="selection" painter="bar"><element name="x"/></element></view>', None, 'unknown element <element>'),
    ('<view name="dialog"/>', None, 'unknown view dialog'),
    ('<view name="list" layout="x"/>', None, 'unknown key view.layout'),
    ('<view name="list"><element name="row"/></view>', None, 'unknown element list.row'),
    ('<view name="list"><element name="icons" grey="1"/></view>', None, 'unknown key list.icons.grey'),
    ('<view name="list"><element name="selection" painter="stops"/></view>', None, 'list.selection.painter: unknown value stops'),
    ('<view name="playing"><element name="title" font="other"/></view>', None, 'playing.title.font: unknown value other'),
    ('<colorScheme name="other-blue"/>', None, 'unknown scheme other-blue'),
    ('<colorScheme name="ivory" sel.edge="#000000"/>', None, 'unknown key scheme.sel.edge'),
    ('<colorSchemes accents="graphite blue"/>', None, 'unknown scheme blue'),
    ('<colorSchemes accents="graphite" derive="gloss"/>', None, 'unknown key colorSchemes.derive'),
    ('<view name="home"><element name="chevron" visible="2"/></view>', None, 'home.chevron.visible: expected 0 or 1'),
    ('<view name="playing"><element name="album" opacity="x"/></view>', None, 'playing.album.opacity: expected an integer'),
    ('<view name="playing"><element name="album" opacity="256"/></view>', None, 'playing.album.opacity: 256 outside 0..255'),
    ('<view name="playing"><element name="album" opacity="-1"/></view>', None, 'playing.album.opacity: -1 outside 0..255'),
    ('<colorScheme name="ivory" fill="eeeeec"/>', None, 'scheme.fill: expected #rrggbb'),
    ('<colorScheme name="ivory" fill="#112233fe"/>', None, 'scheme.fill: expected #rrggbb'),
    ('<option setting="volume"/>', None, 'unknown setting volume'),
    ('<option setting="light" preset="dim"/>', None, 'light: unknown value dim'),
    ('<option setting="light" bright="1"/>', None, 'unknown key option.bright'),
    ('<option setting="home" key="HOME"/>', None, 'home: unknown key HOME'),
    ('<option setting="accent" key="OTHER_ACCENT"/>', None, 'accent: unknown key OTHER_ACCENT'),
    ('', 'id="t" name="T" index="2" extends="other"', 'extends unknown theme other'),
    ('', 'id="T" name="T" index="2"', 'id: expected [a-z][a-z0-9_]*'),
    ('', 'id="t" name="" index="2"', 'name: expected a name'),
    ('', 'id="t" name="T" index="10"', 'index: expected a digit'),
    ('', 'id="t" name="T" index="2" colour="x"', 'unknown key theme.colour'),
    ('<view name="home"><element name="rows" top="8"/></view>', M, 'home.rows.top comes from patch/offsets.inc'),
    ('<colorScheme name="graphite" fill="#eeeeec"/>', M, 'scheme.fill comes from patch/offsets.inc'),
])
def test_resolve_rejects(body, attrs, message):
    assert error(resolved, body, *([attrs] if attrs else [])) == f't.xml:1: {message}'


def test_resolve_rejects_a_cycle():
    a = theme.parse('<theme format="1" id="a" name="A" index="2" extends="b"/>', 'a.xml')
    b = theme.parse('<theme format="1" id="b" name="B" index="3" extends="a"/>', 'b.xml')
    assert error(theme.resolve, a, {'a': a, 'b': b}) == 'a.xml:1: extends cycle at a'


def test_resolve_checks_role_macros(monkeypatch):
    monkeypatch.setitem(theme.ROLE_MACROS, 'minimal', {**theme.ROLE_MACROS['minimal'], 'np_bar': '400'})
    line = theme.load(THEMES/'minimal/theme.xml').line
    assert error(theme.builtins) == f'minimal/theme.xml:{line}: playing.scrubber.height: 400 outside 0..375'


N = 'id="t" name="T" index="2"'
@pytest.mark.parametrize('body, attrs, message', [
    ('<option setting="light"/>', None, 'no accent option'),
    ('<option setting="accent"/>', N, 'accent: no key'),
    ('<option setting="light"/><option setting="light"/><option setting="accent" key="ACCENT"/>', None, 'SETTING_LIGHT declared twice'),
    ('<colorSchemes accents="cobalt"/><option setting="accent" key="ACCENT" default="green"/>', None, 'accent default green is not in colorSchemes'),
    ('<colorSchemes accents="cobalt"/><option setting="accent" key="ACCENT" preset="green"/>', None, 'accent preset green is not in colorSchemes'),
    ('<colorSchemes accents=""/>', None, 'colorSchemes is empty'),
])
def test_validate_rejects(body, attrs, message):
    t = resolved(body, *([attrs] if attrs else []))
    assert error(theme.validate, t, 't.xml') == f't.xml:1: {message}'


def test_builtins_rejects(tmp_path):
    def put(folder, attrs):
        (tmp_path/folder).mkdir(exist_ok=True)
        (tmp_path/folder/'theme.xml').write_text(f'<theme format="1" {attrs}><option setting="accent" key="ACCENT"/></theme>')
    put('a', 'id="a" name="A" index="0"')
    assert [t['id'] for t in theme.builtins(tmp_path)] == ['a']
    put('b', 'id="b" name="B" index="2"')
    assert error(theme.builtins, tmp_path) == 'b/theme.xml:1: index 2, expected 1'
    put('b', 'id="c" name="B" index="1"')
    assert error(theme.builtins, tmp_path) == 'b/theme.xml:1: id c is not its folder name'
