"""tools/theme.py: the Q2 theme XML reader, the schema and the generated theme table."""
import itertools
import re
import shutil
import xml.etree.ElementTree as ET

import pytest

import ipod
import theme
from ipod import Geometry, SET0
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
    ('\nx', 't.xml:2: text between tags'),
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
    ('<theme format="1">\n<view>\n<element>', 't.xml:3: unclosed <element>'),
    ('<theme format="1">x', 't.xml:1: text between tags'),
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


CF_TODAY = {'cf.top': 13, 'cf.x1': 112, 'cf.xs': 48, 'cf.z1': 90, 'cf.zs': 20, 'cf.eye': 300, 'cf.bright1': 192,
            'cf.bright2': 128, 'cf.reflect': 24, 'cf.reflect_top': 48, 'cf.stride': 110, 'cf.radius': 12,
            'cf.name_px': 24, 'cf.artist_px': 20, 'cf.text_y': 212}


MINIMAL = {
    'id': 'minimal', 'index': 0, 'dev': 0, 'name': 'Minimal', 'accents': 0x1ff, 'accent_key': 'MINIMAL_ACCENT',
    'painter[PAINT_SELECTION]': 0, 'chevron_home': 0, 'caps_mark': 1, 'caps_home': 1, 'caps_np': 1,
    'icons_mono': 1, 'icons_badge_muted': 1, 'icons_addon_mono': 1, 'bold': 0, 'np_album_op': 0, 'np_state_op': 0,
    'home_top': 8, 'home_full_row': 375, 'home_text_x': 39, 'home_label_end[0]': 8, 'home_label_end[1]': 33,
    'set_top': 8, 'np_bar': 4, 'np_scrub': 8, 'art_square': 0, 'batt_fill': 0, 'np_layout': 0, 'geom_set': 'ipod',
    'schemes': [{**MINIMAL_ROLES, 'raw_red': int(v == 1), 'card': int(v == 7)} for v in range(9)],
    'opts': [opt('SETTING_LIGHT', preset=0, keep=2), opt('SETTING_ACCENT', default=7, preset=0),
             opt('SETTING_ROUNDED', preset=1), opt('SETTING_HOME', preset=0), opt('SETTING_SHORTCUT'),
             opt('SETTING_BATTERY'), opt('SETTING_TIMETITLE', preset=1), opt('SETTING_NPCENTRED')], **CF_TODAY}
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
    assert [bare(t) for t in theme.builtins()[:2]] == [MINIMAL, CLASSIC]


def test_extends():
    assert bare(resolved('', 'id="t" name="T" index="2" extends="classic"')) == {**CLASSIC, 'id': 't', 'name': 'T', 'index': 2}
    assert resolved('<option setting="accent" key="ACCENT"/>', 'id="t" name="T" index="2" extends="classic"')['opts'] == [opt('SETTING_ACCENT', values=0x17f)]
    t = resolved('<colorSchemes accents="cobalt green"/><option setting="accent" key="ACCENT" default="cobalt"/>',
                 'id="t" name="T" index="2" extends="classic"')
    assert (t['accents'], t['schemes'][0], t['schemes'][4], t['opts']) == (0x110, ZERO, CLASSIC_ROLES, [opt('SETTING_ACCENT', default=4, values=0x110)])
    # resolve() takes it, but builtins() rejects it beside Classic, whose ACCENT takes other colours
    assert resolved('<view name="home"><element name="rows" top="12"/></view>')['home_top'] == 12  # a card theme states numbers
    assert resolved('<colorScheme name="graphite" fill="#112233"/>')['schemes'][0]['fill'] == 0x112233
    assert resolved('<colorScheme name="graphite" fill="#AbCdEf"/>')['schemes'][0]['fill'] == 0xabcdef
    assert resolved('<colorSchemes/>')['accents'] == 0
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
    ('<colorSchemes accents="cobalt"/><colorScheme name="green" muted="#112233"/>', None, 'scheme green is not in colorSchemes'),
    ('<option setting="volume"/>', None, 'unknown setting volume'),
    ('<option setting="light" preset="dim"/>', None, 'light: unknown value dim'),
    ('<option setting="light" bright="1"/>', None, 'unknown key option.bright'),
    ('<option setting="home" key="HOME"/>', None, 'home: unknown key HOME'),
    ('<option setting="accent" key="OTHER_ACCENT"/>', None, 'accent: unknown key OTHER_ACCENT'),
    ('', 'id="t" name="T" index="2" extends="other"', 'extends unknown theme other'),
    ('', 'id="T" name="T" index="2"', 'id: expected [a-z][a-z0-9_]*'),
    ('', 'id="t" name="" index="2"', 'name: expected 1..48 bytes, no control characters'),
    ('', 'id="t" name="T" index="10"', 'index: expected a digit'),
    ('', 'id="t" name="T" index="2" colour="x"', 'unknown key theme.colour'),
    ('', 'name="T" index="2"', 'id: expected [a-z][a-z0-9_]*'),
    ('', 'id="t" name="T"', 'index: expected a digit'),
    ('', 'id="t" name="T" index="2" dev="2"', 'dev: expected 0 or 1'),
    ('<colorScheme/>', None, 'unknown scheme '),
    ('<option/>', None, 'unknown setting '),
    ('<view/>', None, 'unknown view '),
    ('<view name="list"><element/></view>', None, 'unknown element list.'),
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
    monkeypatch.setitem(theme.ROLE_MACROS, 'minimal', {**theme.ROLE_MACROS['minimal'], 'np_bar': 'abs(4)'})
    with pytest.raises(NameError):  # offsets.inc names only, no builtins
        theme.builtins()


N = 'id="t" name="T" index="2"'
@pytest.mark.parametrize('body, attrs, message', [
    ('<option setting="light"/>', None, 'no accent option'),
    ('<option setting="accent"/>', N, 'accent: no key'),
    ('<option setting="light"/><option setting="light"/><option setting="accent" key="ACCENT"/>', None, 'SETTING_LIGHT declared twice'),
    ('<colorSchemes accents="cobalt"/><option setting="accent" key="ACCENT" default="green"/>', None, 'accent default green is not in colorSchemes'),
    ('<colorSchemes accents="cobalt"/><option setting="accent" key="ACCENT" preset="green"/>', None, 'accent preset green is not in colorSchemes'),
    ('<colorSchemes accents="cobalt"/><option setting="accent" key="ACCENT" default="graphite"/>', None, 'accent default graphite is not in colorSchemes'),
    ('<colorSchemes accents=""/>', None, 'colorSchemes is empty'),
])
def test_validate_rejects(body, attrs, message):
    t = resolved(body, *([attrs] if attrs else []))
    assert error(theme.validate, t, 't.xml') == f't.xml:1: {message}'


def test_validate_accepts():
    t = resolved('<colorSchemes accents="cobalt"/><option setting="accent" key="ACCENT" default="cobalt" preset="cobalt"/>')
    assert theme.validate(t, 't.xml') is None


def test_key():
    assert theme.key('a.b.c', 'f', 'bool') == theme.Key('a.b.c', 'f', 'bool', 0, 1, 0, 'live')
    assert theme.key('a.b.c', 'f', 'color').hi == 0xffffff
    assert theme.key('list.selection.painter', 'f', 'enum').hi == 1
    assert theme.key('a.b.c', 'f', 'int', 255).hi == 255


# Each coverflow.* key at its low end: a theme with no base states them all.
CF_LO = '<view name="coverflow">' + ''.join(
    f'<element name="{e}" ' + ' '.join(f'{k.path.rpartition(".")[2]}="{k.lo}"' for k in theme.SCHEMA.values()
                                       if k.path.startswith(f'coverflow.{e}.')) + '/>' for e in ('shelf', 'caption')) + '</view>'


def test_builtins_rejects(tmp_path):
    def put(folder, attrs):
        (tmp_path/folder).mkdir(exist_ok=True)
        (tmp_path/folder/'theme.xml').write_text(f'<theme format="1" {attrs}><option setting="accent" key="ACCENT"/>{CF_LO}</theme>')
    put('a', 'id="a" name="A" index="0"')
    assert [t['id'] for t in theme.builtins(tmp_path)] == ['a']
    put('b', 'id="b" name="B" index="2"')
    assert error(theme.builtins, tmp_path) == 'b/theme.xml:1: index 2, expected 1'
    put('b', 'id="c" name="B" index="1"')
    assert error(theme.builtins, tmp_path) == 'b/theme.xml:1: id c is not its folder name'
    put('b', 'id="b" name="B" index="0"')
    assert error(theme.builtins, tmp_path) == 'b/theme.xml:1: index 0, expected 1'
    (tmp_path/'b'/'theme.xml').write_text('<theme format="1" id="b" name="B" index="1"><option setting="accent"/></theme>')
    assert error(theme.builtins, tmp_path) == 'b/theme.xml:1: accent: no key'


def test_builtins_rejects_a_shared_accent_key(tmp_path):
    """Themes sharing an accent key share its stored value, so they must take the same colours and default."""
    shutil.copytree(THEMES, tmp_path, dirs_exist_ok=True, ignore=shutil.ignore_patterns('nano'))
    def put(folder, attrs, body):
        (tmp_path/folder).mkdir(exist_ok=True)
        (tmp_path/folder/'theme.xml').write_text(f'<theme format="1" {attrs}>{body}</theme>')
    shared = 'id="t" name="T" index="2" extends="classic"'
    put('t', shared, '<colorSchemes accents="cobalt green"/><option setting="accent" key="ACCENT" default="cobalt"/>')
    assert error(theme.builtins, tmp_path) == 't/theme.xml:1: accent key ACCENT is classic\'s, with other colorSchemes or default'
    put('t', shared, '<option setting="accent" key="ACCENT" default="graphite"/>')
    assert [t['id'] for t in theme.builtins(tmp_path)] == ['minimal', 'classic', 't']  # Classic's own: allowed
    put('t', shared, '<option setting="accent" key="ACCENT" default="tidal"/>')
    assert error(theme.builtins, tmp_path) == 't/theme.xml:1: accent key ACCENT is classic\'s, with other colorSchemes or default'


def test_builtins_rejects_a_duplicate_id(tmp_path):
    for folder in ('a', 'b'):
        (tmp_path/folder).mkdir()
        (tmp_path/folder/'theme.xml').write_text(f'<theme format="1" id="a" name="A" index="0"><option setting="accent" key="ACCENT"/>{CF_LO}</theme>')
    assert error(theme.builtins, tmp_path) == "b/theme.xml:1: id a is a/theme.xml's too"


def test_validate_rejects_unset_coverflow():
    """A theme with no base and no coverflow.* has 0 for keys whose range starts at 1 (reflect divides)."""
    t = resolved('<option setting="accent" key="ACCENT"/>', 'id="t" name="T" index="2"')
    assert error(theme.validate, t, 't.xml') == 't.xml:1: coverflow.shelf.eye: 0 outside 1..2046'
    t = resolved(f'<option setting="accent" key="ACCENT"/>{CF_LO}', 'id="t" name="T" index="2"')
    assert theme.validate(t, 't.xml') is None


@pytest.mark.parametrize('name', ['A\x7fB', 'A\x85B', 'x' * 49, 'é' * 25])
def test_resolve_rejects_a_bad_name(name):
    assert error(resolved, '', f'id="t" name="{name}" index="2" extends="minimal"') == 't.xml:1: name: expected 1..48 bytes, no control characters'


def test_resolve_takes_a_long_name():
    assert resolved('', f'id="t" name="{"é" * 24}" index="2" extends="minimal"')['name'] == 'é' * 24


MINIMAL_FIELDS = [('name', '"Minimal"'), ('painter[PAINT_SELECTION]', 'SEL_CARD'), ('chevron_home', 0), ('caps_mark', 1),
                  ('caps_home', 1), ('caps_np', 1), ('icons_mono', 1), ('icons_badge_muted', 1), ('icons_addon_mono', 1),
                  ('bold', 0), ('np_album_op', 0), ('np_state_op', 0), ('home_top', 8), ('home_full_row', 375),
                  ('home_text_x', 39), ('home_label_end[0]', 8), ('home_label_end[1]', 33), ('set_top', 8), ('np_bar', 4),
                  ('np_scrub', 8), ('art_square', 0), ('batt_fill', 0), ('accent_slot', '&st.minimal_accent'), ('accent_key', '"MINIMAL_ACCENT"'),
                  ('schemes', 'minimal_schemes'), ('opts', 'minimal_opts'), ('n_opts', 8), ('np_layout', 'NP_IPOD'),
                  *CF_TODAY.items(), ('geom', 0)]


def test_emit(tmp_path):
    theme.emit(theme.builtins()[:1], tmp_path)
    flags = {1: 2, 7: 1}
    assert (tmp_path/'themes.inc').read_text().startswith('\n'.join([
        '/* tools/theme.py wrote this from assets/themes/<id>/theme.xml; edit those. */',
        'static const scheme_t minimal_schemes[] = {',
        *(f'    {{ .tones = accents[{v}], .flags = {flags.get(v, 0)}, .fill = 0xeeeeec, .chevron = 0x777777, '
          f'.muted = 0x8c8c8c, .bar_light = 0x000000 }},' for v in range(9)),
        '};',
        '_Static_assert(sizeof minimal_schemes / sizeof *minimal_schemes == ACCENT_N, "one scheme per ACCENTS row");',
        'static const option_t minimal_opts[] = {',
        '    { .setting = SETTING_LIGHT, .def = -1, .preset = 0, .keep = 2, .values = 0x0 },',
        '    { .setting = SETTING_ACCENT, .def = 7, .preset = 0, .keep = -1, .values = 0x0 },',
        '    { .setting = SETTING_ROUNDED, .def = -1, .preset = 1, .keep = -1, .values = 0x0 },',
        '    { .setting = SETTING_HOME, .def = -1, .preset = 0, .keep = -1, .values = 0x0 },',
        '    { .setting = SETTING_SHORTCUT, .def = -1, .preset = -1, .keep = -1, .values = 0x0 },',
        '    { .setting = SETTING_BATTERY, .def = -1, .preset = -1, .keep = -1, .values = 0x0 },',
        '    { .setting = SETTING_TIMETITLE, .def = -1, .preset = 1, .keep = -1, .values = 0x0 },',
        '    { .setting = SETTING_NPCENTRED, .def = -1, .preset = -1, .keep = -1, .values = 0x0 },',
        '};',
        'static const theme_t themes[THEME_N] = {',
        '    {', *(f'        .{k} = {v},' for k, v in MINIMAL_FIELDS), '    },',
        '};', '']))


def test_emit_both(tmp_path):
    theme.emit(theme.builtins()[:2], tmp_path)
    text = (tmp_path/'themes.inc').read_text()
    assert [l.split(' = ')[0] for l in text.splitlines() if l.startswith('static const')] == [
        'static const scheme_t minimal_schemes[]', 'static const option_t minimal_opts[]',
        'static const scheme_t classic_schemes[]', 'static const option_t classic_opts[]',
        'static const theme_t themes[THEME_N]', 'static const volatile short geom_vals[GEOM_N][V_N]']
    assert '    { .tones = accents[7], .flags = 0, .fill = 0x000000, .chevron = 0x000000, .muted = 0x000000, .bar_light = 0x000000 },\n    { .tones = accents[8]' in text
    assert '    { .setting = SETTING_ACCENT, .def = 0, .preset = 4, .keep = -1, .values = 0x17f },' in text
    assert '        .accent_slot = &st.accent,\n        .accent_key = "ACCENT",\n        .schemes = classic_schemes,' in text
    assert '    },\n};\nenum { GEOM_IPOD, GEOM_N };\n' in text


def test_playing_layout_builtins_ipod():
    assert [t['np_layout'] for t in theme.builtins()[:2]] == [0, 0]


def test_playing_layout_resolves_centred():
    t = resolved('<view name="playing" layout="centred"/>'); theme.validate(t, 't.xml')
    assert t['np_layout'] == 1


def test_playing_layout_rejects_unknown():
    assert error(resolved, '<view name="playing" layout="other"/>') == 't.xml:1: playing.layout: unknown value other'


def test_emit_np_layout(tmp_path):
    theme.emit(theme.builtins()[:2], tmp_path)
    assert (tmp_path/'themes.inc').read_text().count('.np_layout = NP_IPOD,') == 2


def test_view_attr():
    assert theme.view_attr('list.selection.painter') and theme.view_attr('home.rows.top')
    assert not theme.view_attr('playing.layout') and not theme.view_attr('scheme.fill')
    assert not theme.view_attr('scheme.sel.stops')  # a scheme role has two dots too (Task 11)


# Enum fields and value counts come out as C names, so a reordered or added value is a compile error.
def test_emit_names_enums_and_counts(tmp_path):
    inc = boot_inc(tmp_path, theme.builtins())
    assert inc.count('.painter[PAINT_SELECTION] = SEL_CARD,') == 1 and inc.count('.painter[PAINT_SELECTION] = SEL_BAR,') == 2
    assert inc.count('.np_layout = NP_IPOD,') == 3 and '.np_layout = 0,' not in inc
    for name, (_, values) in theme.SETTINGS.items():
        assert f'#define SETTING_N_{name.upper()} {len(values)}\n' in inc


def with_role(monkeypatch, **cf):
    monkeypatch.setitem(theme.ROLE_MACROS, 'minimal', {**theme.ROLE_MACROS['minimal'], **{f'cf.{k}': str(v) for k, v in cf.items()}})


@pytest.mark.parametrize('field,value', [('eye', 2047), ('eye', 0), ('xs', 800), ('zs', 800), ('x1', 1024), ('reflect', 0),
    ('bright2', 0), ('text_y', 209), ('name_px', 33), ('artist_px', 29), ('radius', 84), ('stride', 0)])
def test_coverflow_range_rejects(monkeypatch, field, value):
    with_role(monkeypatch, **{field: value})
    with pytest.raises(theme.ThemeError, match=rf'coverflow\.\w+\.{field}: {value} outside'):
        theme.builtins()


@pytest.mark.parametrize('field,value,message', [
    ('cf.top', 27, 'top \\+ 160 \\+ reflect = 211 exceeds 210'), ('cf.reflect', 38, 'top \\+ 160 \\+ reflect = 211 exceeds 210'),
    ('cf.bright2', 193, 'bright2 193 exceeds bright1 192'), ('cf.text_y', 231, 'text_y \\+ 60 = 291 exceeds the client height 290')])
def test_coverflow_cross_rejects(field, value, message):
    with pytest.raises(theme.ThemeError, match=message):
        theme.validate({**theme.builtins()[0], field: value}, 't.xml')


def test_coverflow_caption_follows_the_bar(monkeypatch):
    monkeypatch.setitem(theme.GEOMETRIES, 'tall', Geometry(name='tall', bar=34))
    t = {**theme.builtins()[0], 'geom_set': 'tall'}
    with pytest.raises(theme.ThemeError, match='text_y \\+ 60 = 287 exceeds the client height 286'):
        theme.validate({**t, 'cf.text_y': 227}, 't.xml')
    theme.validate({**t, 'cf.text_y': 226}, 't.xml')


def test_coverflow_edges_pass(monkeypatch):   # each limit itself is allowed
    for edge in ({'cf.top': 26}, {'cf.text_y': 230}, {'cf.bright2': 192}):
        theme.validate({**theme.builtins()[0], **edge}, 't.xml')
    with_role(monkeypatch, eye=2046, x1=1023, xs=0)
    theme.builtins()


def test_emit_cf(tmp_path):
    theme.emit(theme.builtins()[:2], tmp_path)
    text = (tmp_path/'themes.inc').read_text()
    assert text.count('.cf.eye = 300,') == 2 and text.count('.cf.text_y = 212,') == 2


TEST = Geometry(name='test', pitch=28)


@pytest.fixture
def test_set(monkeypatch):
    monkeypatch.setitem(theme.GEOMETRIES, 'test', TEST)


def minimal_with(geometry):
    text = (theme.ROOT/'assets/themes/minimal/theme.xml').read_text()
    text = re.sub(r'(<theme\b[^>]*>)', r'\1' + geometry, text, count=1)
    t = theme.resolve(theme.parse(text, 'minimal.xml'), {})
    theme.validate(t, 'minimal.xml')
    return t


def test_builtins_are_set0():
    assert [theme.geometry(t) for t in theme.builtins()[:2]] == [SET0, SET0]
    assert theme.geometry_sets(theme.builtins()[:2]) == [SET0]
    assert minimal_with('<geometry set="ipod"/>') == theme.builtins()[0]


@pytest.mark.parametrize('geometry, message', [
    ('<geometry set="other"/>', 'unknown geometry set "other"'),
    ('<geometry set="ipod" bar="26"/>', 'unknown key geometry.bar')])
def test_geometry_rejects(geometry, message):
    with pytest.raises(theme.ThemeError, match=message):
        minimal_with(geometry)


def test_geometry_sets_set0_first(test_set):
    m, c = theme.builtins()[:2]
    assert theme.geometry_sets([dict(m, geom_set='test'), c]) == [SET0, TEST]
    assert theme.geometry_sets([dict(m, geom_set='test')]) == [SET0, TEST]


def test_geometry_sets_refuse_a_wide_value(monkeypatch):
    monkeypatch.setitem(theme.GEOMETRIES, 'wide', Geometry(name='wide', pitch=0x8000))
    with pytest.raises(ValueError, match='wide: pitch 32768 does not fit an immediate'):
        theme.geometry_sets([dict(theme.builtins()[0], geom_set='wide')])


def test_shared_site_takes_one_value(monkeypatch):
    monkeypatch.setitem(theme.GEOMETRIES, 'split', Geometry(name='split', pitch=28, art_pitch=52))
    monkeypatch.setitem(theme.AUDIT, 'immediates', [{'value': 'pitch', 'purpose': 'x', 'page': 'shared', 'sites': [['0x4a2c5c', '0x2402004e']]}])
    with pytest.raises(ValueError, match='split: pitch and art_pitch differ at shared site 0x4a2c5c'):
        theme.geometry_sets([dict(theme.builtins()[0], geom_set='split')])


def combinations(t):
    """Every value combination of t's options: each setting's names, narrowed by a non-zero values mask."""
    names = {macro: v for macro, v in theme.SETTINGS.values()}
    return itertools.product(*([n for i, n in enumerate(names[o['setting']]) if not o['values'] or o['values'] >> i & 1]
                               for o in t['opts']))


def test_minimal_classic_live_every_combination():
    # Options carry no variants yet, so each combination resolves to the same two dicts; the loop
    # is what makes a later option that feeds a boot-class field fail here.
    m, c = theme.builtins()[:2]
    cm, cc = list(combinations(m)), list(combinations(c))
    assert (len(cm), len(cc)) == (3*9*2*3*7*4*2*2, 3*8*2*3*7*4*2*2)  # classic's accent mask 0x17f keeps 8 of 9
    for _ in itertools.chain(cm, cc):
        assert theme.is_live(m, c) and theme.is_live(c, m)


def test_is_live_needs_one_set(test_set):
    m, c = theme.builtins()[:2]
    assert not theme.is_live(m, dict(c, geom_set='test'))


def boot_inc(tmp_path, themes, **kw):
    theme.emit(themes, tmp_path, **kw)
    return (tmp_path/'themes.inc').read_text()


def test_emit_boot_tables(tmp_path):
    inc = boot_inc(tmp_path, theme.builtins()[:2])
    names = ipod.VALUE_NAMES
    sites = [(int(a, 16), g['value']) for g in ipod.AUDIT['immediates'] if isinstance(g['value'], str) for a, _ in g['sites']]
    assert len(sites) == 58
    assert inc.index('enum { GEOM_IPOD, GEOM_N };') > inc.index('static const theme_t themes[THEME_N]')
    assert 'enum { ' + ', '.join(f'G_{n.upper()}' for n in names) + ', G_N };' in inc
    assert '#define BOOT_SITES 58' in inc and '#define CLIENT_H_MAX 290' in inc
    assert 'const boot_site_t boot_sites[BOOT_SITES] = {' + ', '.join(f'{{{a:#x}, G_{v.upper()}}}' for a, v in sites) + '};' in inc
    row = ', '.join(str(ipod.immediate_values(SET0)[n]) for n in names)
    assert f'const volatile unsigned short boot_values[GEOM_N][G_N] = {{{{{row}}}}};' in inc
    assert 'const char *const geom_names[GEOM_N] = {"ipod"};' in inc
    assert 'const volatile ui_redirect_t ui_redirects[] = {{0, 0}, {0, 0}};' in inc
    assert re.findall(r'\.geom = (\d+)', inc) == ['0', '0']
    assert all(s in inc for s in geom_vals_text([SET0])) and '#define THEME_GEOM_OFF 4' in inc


def geom_vals_text(sets):
    return ('enum { ' + ', '.join(f'V_{n.upper()}' for n in ipod.GEOM_VALS) + ', V_N };',
            'geom_vals[GEOM_N][V_N] = {' + ', '.join('{' + ', '.join(str(getattr(g, n)) for n in ipod.GEOM_VALS) + '}' for g in sets) + '};')


def test_emit_geom_vals(tmp_path):
    inc = boot_inc(tmp_path, theme.builtins())
    assert all(s in inc for s in geom_vals_text([SET0, ipod.NANO, ipod.NANO_TEXT]))
    assert ipod.GEOM_VALS[:3] == ('bar', 'set_row', 'set_icon')
    assert [[getattr(g, n) for n in ipod.GEOM_VALS[:3]] for g in (SET0, ipod.NANO)] == [[30, 68, 40], [26, 28, 0]]


def test_emit_refuses_a_long_redirect(tmp_path):
    # 'nano-text_' + 53 characters is 63 bytes plus the NUL: it fits; 54 does not.
    boot_inc(tmp_path, theme.builtins(), redirects={'x' * 53: 2})
    with pytest.raises(ValueError, match='exceeds ringnav_ui_load'):
        boot_inc(tmp_path, theme.builtins(), redirects={'x' * 54: 2})


def test_emit_second_set(tmp_path, test_set):
    m, c = theme.builtins()[:2]
    inc = boot_inc(tmp_path, [m, dict(c, geom_set='test')], redirects={'system_bar': 2, 'dialog/volume_dialog': 2})
    assert 'enum { GEOM_IPOD, GEOM_TEST, GEOM_N };' in inc and '{"ipod", "test"}' in inc
    rows = ['{' + ', '.join(str(ipod.immediate_values(g)[n]) for n in ipod.VALUE_NAMES) + '}' for g in (SET0, TEST)]
    assert '= {' + ', '.join(rows) + '};' in inc
    assert 'ui_redirects[] = {{"dialog/volume_dialog", 2}, {"system_bar", 2}, {0, 0}, {0, 0}};' in inc
    assert re.findall(r'\.geom = (\d+)', inc) == ['0', '1']


def test_emit_refuses_a_set0_redirect(tmp_path):
    with pytest.raises(ValueError, match='system_bar: SET0 never redirects'):
        boot_inc(tmp_path, theme.builtins(), redirects={'system_bar': 1})


def test_nano_builtin():
    m, c, n = theme.builtins()
    assert (n['id'], n['name'], n['index'], n['dev'], n['geom_set'], n['accent_key'], n['accents']) == ('nano', 'Nano', 2, 1, 'nano', 'NANO_ACCENT', 0x17f)
    assert n['opts'] == [opt('SETTING_ACCENT', default=4, preset=4, values=0x17f), opt('SETTING_HOME'), opt('SETTING_BATTERY')]
    assert theme.geometry_sets([m, c, n]) == [SET0, ipod.NANO, ipod.NANO_TEXT]
    assert (theme.offer([m, c, n], False), theme.offer([m, c, n], True)) == (2, 3)


def test_dev_themes_come_last(tmp_path):
    shutil.copytree(THEMES, tmp_path, dirs_exist_ok=True)
    (tmp_path/'t').mkdir()
    (tmp_path/'t'/'theme.xml').write_text('<theme format="1" id="t" name="T" index="3" extends="classic"><option setting="accent" key="ACCENT" default="graphite"/></theme>')
    assert error(theme.builtins, tmp_path) == "t/theme.xml:1: index 3: an offered theme after the dev theme nano"


def test_emit_nano_sets(tmp_path):
    inc = boot_inc(tmp_path, theme.builtins())
    assert 'enum { GEOM_IPOD, GEOM_NANO, GEOM_NANO_TEXT, GEOM_N };' in inc and '#define CLIENT_H_MAX 294' in inc
    assert 'const char *const geom_names[GEOM_N] = {"ipod", "nano", "nano-text"};' in inc
    assert re.findall(r'\.geom = (\d+)', inc) == ['0', '0', '1'] and '.accent_slot = &st.nano_accent,' in inc
