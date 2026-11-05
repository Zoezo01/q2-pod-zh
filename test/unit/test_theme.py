"""tools/theme.py: the Q2 theme XML reader, the schema and the generated theme table."""
import dataclasses
import itertools
import re
import shutil
import xml.etree.ElementTree as ET

import pytest

import ipod
import theme
from ipod import Geometry, SET0
from theme import ThemeError

NANO, NANO_TEXT = (theme.sets(theme.builtins()[2])[s] for s in ('nano', 'nano-text'))  # Nano's sets: its theme.xml


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
ZERO = {**dict.fromkeys(('card', 'raw_red', 'fill', 'chevron', 'muted', 'bar_light'), 0),
        'sel': (), 'sel_top': 0, 'sel_edge': 0, 'sel_text': 0, 'bar': (), 'bar_line': 0, 'mode': 0, 'paper': 0xf4f4f4, 'ink': 0x161616, 'light': {}}
MINIMAL_ROLES = {**ZERO, 'fill': 0xeeeeec, 'chevron': 0x777777, 'muted': 0x8c8c8c}
CLASSIC_ROLES = {**ZERO, 'bar_light': 0xdedede}


def opt(setting, default=-1, preset=-1, keep=-1, values=0):
    return {'setting': setting, 'def': default, 'preset': preset, 'keep': keep, 'values': values}


def own(key, label, names, sets, default=-1, preset=-1, keep=-1):  # a theme's own option, as resolve() keeps it
    return {**opt('SETTING_OWN', default, preset, keep), 'key': key, 'label': label, 'names': list(names), 'sets': list(sets)}


CF_TODAY = {'cf.top': 13, 'cf.x1': 112, 'cf.xs': 48, 'cf.z1': 90, 'cf.zs': 20, 'cf.eye': 300, 'cf.bright1': 192,
            'cf.bright2': 128, 'cf.reflect': 24, 'cf.reflect_top': 48, 'cf.stride': 110, 'cf.radius': 12,
            'cf.name_px': 24, 'cf.artist_px': 20, 'cf.text_y': 212}


MINIMAL = {
    'id': 'minimal', 'index': 0, 'dev': 0, 'name': 'Minimal', 'accents': 0x1ff, 'accent_key': 'MINIMAL_ACCENT',
    'painter[PAINT_SELECTION]': 0, 'painter[PAINT_BAR]': 0, 'painter[PAINT_BATTERY]': 0, 'chevron_home': 0, 'chevron_sel': 0, 'caps_mark': 1, 'caps_home': 1, 'caps_np': 1,
    'icons_mono': 1, 'icons_badge_muted': 1, 'icons_addon_mono': 1, 'font[ROLE_NP]': 0, 'np_album_op': 0, 'np_state_op': 0,
    'home_top': 8, 'home_full_row': 375, 'home_text_x': 39, 'home_label_end[0]': 8, 'home_label_end[1]': 33,
    'home_pane': (), 'pane_shadow': 0, 'pane_shadow_w': 0, 'pane_shadow_a': 0, 'pane_light': 0, 'pane_light_a': 0,
    'set_top': 8, 'set_text': 0, 'np_bar': 4, 'np_scrub': 8, 'art_square': 0, 'batt_fill': 0, 'font[ROLE_TITLE]': 0, 'font[ROLE_META]': 0, 'font_px[ROLE_TITLE]': 0, 'font_px[ROLE_META]': 0, 'font[ROLE_BAR]': 0, 'font_px[ROLE_BAR]': 0, 'batt': (), 'batt_line': 0, 'state_color': 0, 'light': {}, 'np_layout': 0, 'geom_set': 'ipod', 'geoms': {},
    'schemes': [{**MINIMAL_ROLES, 'raw_red': int(v == 1), 'card': int(v == 7)} for v in range(9)],
    'opts': [opt('SETTING_LIGHT', preset=0, keep=2), opt('SETTING_ACCENT', default=7, preset=0),
             opt('SETTING_ROUNDED', preset=1), opt('SETTING_HOME', preset=0), opt('SETTING_SHORTCUT'),
             opt('SETTING_BATTERY'), opt('SETTING_TIMETITLE', preset=1), opt('SETTING_NPCENTRED')], 'undeclared': [], **CF_TODAY}
CLASSIC = {
    **MINIMAL, 'id': 'classic', 'index': 1, 'name': 'Classic', 'accents': 0x17f, 'accent_key': 'ACCENT',
    'painter[PAINT_SELECTION]': 1, 'chevron_home': 1, 'caps_mark': 0, 'caps_home': 0, 'caps_np': 0,
    'icons_mono': 0, 'icons_badge_muted': 0, 'icons_addon_mono': 0, 'font[ROLE_NP]': 1, 'np_album_op': 255, 'np_state_op': 255,
    'home_top': 0, 'home_full_row': 369, 'home_text_x': 33, 'home_label_end[0]': 33, 'home_label_end[1]': 33,
    'set_top': 0, 'np_bar': 8, 'np_scrub': 8, 'art_square': 1, 'batt_fill': 0x34c759,
    'schemes': [ZERO if v == 7 else {**CLASSIC_ROLES, 'raw_red': int(v == 1)} for v in range(9)],
    'opts': [opt('SETTING_LIGHT', preset=1, keep=2), opt('SETTING_ACCENT', default=0, preset=4, values=0x17f),
             opt('SETTING_ROUNDED', preset=0), opt('SETTING_HOME', preset=0), opt('SETTING_SHORTCUT'),
             opt('SETTING_BATTERY'), opt('SETTING_TIMETITLE', preset=0), opt('SETTING_NPCENTRED')], 'undeclared': []}


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
    ('<view name="list"><element name="tile"/></view>', None, 'unknown element list.tile'),
    ('<view name="list"><element name="icons" grey="1"/></view>', None, 'unknown key list.icons.grey'),
    ('<view name="list"><element name="selection" painter="glass"/></view>', None, 'list.selection.painter: unknown value glass'),
    ('<view name="playing"><element name="title" font="serif"/></view>', None, 'playing.title.font: unknown value serif'),
    ('<colorScheme name="nano-blue"/>', None, 'unknown scheme nano-blue'),
    ('<colorScheme name="ivory" sel.glow="#000000"/>', None, 'unknown key scheme.sel.glow'),
    ('<colorSchemes accents="graphite blue"/>', None, 'unknown scheme blue'),
    ('<colorSchemes accents="graphite" tint="1"/>', None, 'unknown key colorSchemes.tint'),
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
    ('<option setting="accent" key="HOME"/>', None, "HOME: the payload's key"),
    ('', 'id="t" name="T" index="2" extends="pico"', 'extends unknown theme pico'),
    ('', 'id="T" name="T" index="2"', 'id: expected [a-z][a-z0-9_]*'),
    ('', 'id="t" name="" index="2"', 'name: expected 1..48 bytes, no control characters'),
    ('', 'id="t" name="T" index="10"', 'index: expected a digit'),
    ('', 'id="t" name="T" index="2" colour="x"', 'unknown key theme.colour'),
    ('', 'name="T" index="2"', 'id: expected [a-z][a-z0-9_]*'),
    ('', 'id="t" name="T"', 'index: expected a digit'),
    ('', 'id="t" name="T" index="2" dev="2"', 'dev: expected 0 or 1'),
    ('<colorScheme name=""/>', None, 'unknown scheme '),
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
A = '<option setting="accent" key="ACCENT"/>'
V = '<value label="Off" set="ipod"/><value label="On" set="ipod"/>'
P = '<geometry name="pico" bar="26"/>'
@pytest.mark.parametrize('body, attrs, message', [
    ('<option setting="light"/>', None, 'no accent option'),
    ('<option setting="accent"/>', N, 'accent: no key'),
    ('<option setting="light"/><option setting="light"/><option setting="accent" key="ACCENT"/>', None, 'SETTING_LIGHT declared twice'),
    ('<colorSchemes accents="cobalt"/><option setting="accent" key="ACCENT" default="green"/>', None, 'accent default green is not in colorSchemes'),
    ('<colorSchemes accents="cobalt"/><option setting="accent" key="ACCENT" preset="green"/>', None, 'accent preset green is not in colorSchemes'),
    ('<colorSchemes accents="cobalt"/><option setting="accent" key="ACCENT" default="graphite"/>', None, 'accent default graphite is not in colorSchemes'),
    ('<colorSchemes accents=""/>', None, 'colorSchemes is empty'),
    (A + f'<option key="ART" label="Art">{V}</option><option key="ART" label="Art">{V}</option>', None, 'ART declared twice'),
    ('<option setting="accent" key="ART"/>' + f'<option key="ART" label="Art">{V}</option>', None, 'ART declared twice'),
    (A + f'<option key="ART" label="Art">{V}</option><option key="AUX" label="Aux">{V}</option>', None,
     'one option per theme selects the geometry set'),
    (A + P + '<option key="ART" label="Art" default="On"><value label="Off" set="ipod"/><value label="On" set="pico"/></option>', None,
     "ART: default On boots pico, the theme's geometry is ipod"),
    (A + P + '<option key="ART" label="Art" default="Off"><value label="Off" set="ipod"/><value label="On" set="pico"/></option>', None,
     "ART: set pico has a 26 px bar, the theme's is 30"),
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
    assert theme.key('list.selection.painter', 'f', 'enum').hi == 2
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


MINIMAL_FIELDS = [('name', '"Minimal"'), ('painter[PAINT_SELECTION]', 'SEL_CARD'), ('painter[PAINT_BAR]', 'BAR_FLAT'), ('painter[PAINT_BATTERY]', 'BATT_STOCK'), ('chevron_home', 0), ('chevron_sel', 0), ('caps_mark', 1),
                  ('caps_home', 1), ('caps_np', 1), ('icons_mono', 1), ('icons_badge_muted', 1), ('icons_addon_mono', 1),
                  ('font[ROLE_NP]', 'FONT_DEFAULT'), ('np_album_op', 0), ('np_state_op', 0), ('home_top', 8), ('home_full_row', 375),
                  ('home_text_x', 39), ('home_label_end[0]', 8), ('home_label_end[1]', 33), ('home_pane', '{0}'), ('pane_shadow', 0), ('pane_shadow_w', 0), ('pane_shadow_a', 0), ('pane_light', 0), ('pane_light_a', 0), ('set_top', 8), ('set_text', 0), ('np_bar', 4),
                  ('np_scrub', 8), ('art_square', 0), ('batt_fill', 0), ('font[ROLE_TITLE]', 'FONT_DEFAULT'), ('font[ROLE_META]', 'FONT_DEFAULT'),
                  ('font_px[ROLE_TITLE]', 0), ('font_px[ROLE_META]', 0), ('font[ROLE_BAR]', 'FONT_DEFAULT'), ('font_px[ROLE_BAR]', 0),
                  ('batt', '{0}'), ('batt_line', '{0, 0}'), ('state_color', 0),
                  ('accent_slot', '&st.minimal_accent'), ('accent_key', '"MINIMAL_ACCENT"'),
                  ('schemes', 'minimal_schemes'), ('light', 'minimal_schemes'), ('n_schemes', 9), ('opts', 'minimal_opts'), ('n_opts', 8),
                  ('undeclared', 0), ('np_layout', 'NP_IPOD'),
                  *CF_TODAY.items(), ('geom', 0)]


def test_emit(tmp_path):
    theme.emit(theme.builtins()[:1], tmp_path)
    flags = {1: 2, 7: 1}
    assert (tmp_path/'themes.inc').read_text().startswith('\n'.join([
        '/* tools/theme.py wrote this from assets/themes/<id>/theme.xml; edit those. */',
        'static const scheme_t minimal_schemes[] = {',
        *(f'    {{ .tones = accents[{v}], .flags = {flags.get(v, 0)}, .fill = 0xeeeeec, .chevron = 0x777777, '
          f'.muted = 0x8c8c8c, .bar_light = 0x000000, .sel = {{0}}, .sel_top = 0x000000, .sel_edge = 0x000000, '
          f'.sel_text = 0x000000, .mode = SCHEME_DERIVE, .paper = 0xf4f4f4, .ink = 0x161616, .bar = {{0}}, .bar_line = 0x000000 }},' for v in range(9)),
        '};',
        '_Static_assert(sizeof minimal_schemes / sizeof *minimal_schemes >= ACCENT_N, "one scheme per ACCENTS row, then the theme\'s own");',
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
    assert ('    { .tones = accents[7], .flags = 0, .fill = 0x000000, .chevron = 0x000000, .muted = 0x000000, .bar_light = 0x000000, '
            '.sel = {0}, .sel_top = 0x000000, .sel_edge = 0x000000, .sel_text = 0x000000, .mode = SCHEME_DERIVE, .paper = 0xf4f4f4, '
            '.ink = 0x161616, .bar = {0}, .bar_line = 0x000000 },\n    { .tones = accents[8]') in text
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
    assert inc.count('.painter[PAINT_SELECTION] = SEL_CARD,') == 1 and inc.count('.painter[PAINT_SELECTION] = SEL_BAR,') == 1
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
    """Every value combination of t's options as {option: value}: a bound setting's names (narrowed by a values
    mask) under its SETTINGS name, an own option's labels under its key."""
    names = {macro: (name, v) for name, (macro, v) in theme.SETTINGS.items()}
    axes = [(o['key'], o['names']) if o.get('key') else names[o['setting']] for o in t['opts']]
    return [dict(zip([a for a, _ in axes], combo)) for combo in itertools.product(
        *([x for i, x in enumerate(v) if not o['values'] or o['values'] >> i & 1] for (_, v), o in zip(axes, t['opts'])))]


def test_live_every_combination():
    m, c, n = theme.builtins()
    cm, cc, cn = combinations(m), combinations(c), combinations(n)
    assert (len(cm), len(cc)) == (3*9*2*3*7*4*2*2, 3*8*2*3*7*4*2*2) and cn  # classic's accent mask 0x17f keeps 8 of 9
    assert all(theme.is_live(m, c, a, cc[0]) for a in cm) and all(theme.is_live(c, m, b, cm[0]) for b in cc)
    assert not any(theme.is_live(m, n, cm[0], b) or theme.is_live(c, n, cc[0], b) for b in cn)
    on, off = ([b for b in cn if b['ROWART'] == v] for v in ('On', 'Off'))
    assert on and off and not theme.is_live(n, n, on[0], off[0]) and theme.is_live(n, n, on[0], on[-1])


def test_is_live_needs_one_set(test_set):
    m, c = theme.builtins()[:2]
    assert not theme.is_live(m, dict(c, geom_set='test'))


NB = '#08a6e7 0, #009ade 17, #008ade 38, #007dde 58, #006dd6 79, #0065d6 100'
NB_STOPS = ((0, 0x08a6e7), (17, 0x009ade), (38, 0x008ade), (58, 0x007dde), (79, 0x006dd6), (100, 0x0065d6))


def test_stops_resolve():
    t = resolved(f'<colorScheme name="cobalt" sel.stops="{NB}" sel.top="#94c7de" sel.edge="#004184" sel.text="#FFFFFF"/>')
    s = t['schemes'][4]
    assert (s['sel'], s['sel_top'], s['sel_edge'], s['sel_text']) == (NB_STOPS, 0x94c7de, 0x004184, 0xffffff)
    assert t['schemes'][0]['sel'] == () and t['schemes'][0]['sel_top'] == t['schemes'][0]['sel_edge'] == 0
    every = resolved('<colorScheme sel.text="#ffffff"/>')  # no name: every scheme the theme has
    assert {s['sel_text'] for s in every['schemes']} == {0xffffff}


@pytest.mark.parametrize('stops, message', [
    ('#94c7de 0', '2 to 8 stops'),
    (', '.join(f'#000000 {p}' for p in (0, 10, 20, 30, 40, 50, 60, 70, 100)), '2 to 8 stops'),
    ('#94c7de 0, #0065d6 50', 'positions must rise from 0 to 100'),
    ('#94c7de 10, #0065d6 100', 'positions must rise from 0 to 100'),
    ('#94c7de 0, #08a6e7 40, #009ade 40, #0065d6 100', 'positions must rise from 0 to 100'),
    ('#94c7de 0, #0065d6 100, #000000 60', 'positions must rise from 0 to 100'),
    ('94c7de 0, #0065d6 100', 'expected "#rrggbb N, ..."'),
    ('#94c7de0, #0065d6 100', 'expected "#rrggbb N, ..."'),
    ('#94c7defe 0, #0065d6 100', 'expected "#rrggbb N, ..."'),
    ('#94c7de 0, #0065d6 1000', 'expected "#rrggbb N, ..."'),
])
def test_stops_reject(stops, message):
    assert error(resolved, f'<colorScheme name="cobalt" sel.stops="{stops}"/>') == f't.xml:1: scheme.sel.stops: {message}'


def test_emit_stops(tmp_path):
    inc = boot_inc(tmp_path, theme.builtins())
    # no closing ' }': later roles (Task 12's mode, Task 13's label) follow sel_text on the line
    assert ('.sel = {6, {0, 17, 38, 58, 79, 100}, {0x08a6e7, 0x009ade, 0x008ade, 0x007dde, 0x006dd6, '
            '0x0065d6}}, .sel_top = 0x94c7de, .sel_edge = 0x004184, .sel_text = 0xffffff') in inc
    assert inc.count('.painter[PAINT_SELECTION] = SEL_STOPS,') == 1


def test_bar_painter_and_scheme_bar_roles():
    t = resolved('<colorScheme name="graphite" bar.stops="#ffffff 0, #9c9e9c 100" bar.line="#5a595a"/>'
                 '<view name="bar"><element name="fill" painter="gloss"/></view>')
    assert t['painter[PAINT_BAR]'] == 1 and t['schemes'][0]['bar'] == ((0, 0xffffff), (100, 0x9c9e9c))
    assert t['schemes'][0]['bar_line'] == 0x5a595a
    assert error(resolved, '<view name="bar"><element name="fill" painter="shiny"/></view>') == \
        't.xml:1: bar.fill.painter: unknown value shiny'


def test_bar_battery_painter_rejects():
    assert error(resolved, '<view name="bar"><element name="battery" painter="segments"/></view>') == \
        't.xml:1: bar.battery.painter: unknown value segments'


def test_theme_level_halves():
    nano = theme.builtins()[2]
    assert nano['batt_line'] == 0 and nano['light']['batt_line'] == 0x636163  # light stated, dark derived
    assert theme.half(nano, False, 0xffffff, 0)['batt_line'] == 0x9c9e9c
    assert error(resolved, '<view name="bar"><element name="battery" light.painter="nano"/></view>') == \
        't.xml:1: bar.battery.painter: one value for both halves'


def test_scheme_mode_paper_ink():
    t = resolved('<colorScheme mode="light" light.paper="#ffffff" light.ink="#000000"/>')
    assert {(s['mode'], s['paper'], s['ink']) for s in t['schemes']} == {(1, 0xffffff, 0)}
    minimal, classic, nano = theme.builtins()
    assert {(s['mode'], s['paper'], s['ink']) for s in minimal['schemes']} == {(0, 0xf4f4f4, 0x161616)}
    assert {(s['mode'], s['paper'], s['ink']) for s in nano['schemes'] if s['sel']} == {(0, 0xffffff, 0)}
    assert (minimal['undeclared'], classic['undeclared']) == ([], [])
    assert nano['undeclared'] == ['SETTING_ROUNDED', 'SETTING_NPCENTRED']
    assert nano['opts'][0] == opt('SETTING_LIGHT', preset=1, keep=2)
    assert not (theme.shared(nano) or not theme.shared(minimal) or not theme.shared(classic))


def test_halves():
    # a stated half is kept; the other is the flip (light) or its inverse (dark) of its neutrals by the
    # light paper and ink (here the defaults, #f4f4f4 and #161616); hues stay; unset stays unset
    s = resolved('<colorScheme name="cobalt" sel.top="#404040" light.sel.edge="#c0c0c0" sel.text="#ffffff" '
                 'light.sel.text="#ffffff" sel.stops="#94c7de 0, #404040 100"/>')['schemes'][4]
    dark, light = theme.scheme_half(s, False), theme.scheme_half(s, True)
    assert (dark['paper'], dark['ink'], light['paper'], light['ink']) == (0, 0xffffff, 0xf4f4f4, 0x161616)
    assert (dark['sel_top'], light['sel_top']) == (0x404040, 0xbdbdbd)     # 0xf4 - 0x40 * (0xf4 - 0x16) // 255
    assert (dark['sel_edge'], light['sel_edge']) == (0x3b3b3b, 0xc0c0c0)   # (0xf4 - 0xc0) * 255 // (0xf4 - 0x16)
    assert (dark['sel_text'], light['sel_text']) == (0xffffff, 0xffffff)   # both stated
    assert light['sel'] == ((0, 0x94c7de), (100, 0xbdbdbd))                # the hue kept, the grey flipped
    assert theme.scheme_half(theme.zero_scheme(), True)['sel_top'] == 0    # unset stays unset
    assert theme.flip(0x94c7de, 0xffffff, 0) == 0x94c7de
    assert theme.flip(0x636163, 0xffffff, 0) == 0x9c9e9c and theme.unflip(0x9c9e9c, 0xffffff, 0) == 0x636163


@pytest.mark.parametrize('body, message', [
    ('<colorScheme name="cobalt" light.paper="#ff0000"/>', 'scheme.paper: expected a grey'),
    ('<colorScheme light.ink="#000001"/>', 'scheme.ink: expected a grey'),
    ('<colorScheme light.paper="#101010" light.ink="#f0f0f0"/>', 'scheme.paper: must be lighter than ink'),
    ('<colorScheme mode="light"/>', 'light: a scheme with its own mode has no Light Mode option'),
])
def test_validate_rejects_mode(body, message):
    assert error(theme.validate, resolved(body), 't.xml') == f't.xml:1: {message}'


@pytest.mark.parametrize('body, message', [
    ('<colorScheme paper="#ffffff"/>', "scheme.paper: the dark half's is #000000; write light.paper"),
    ('<colorScheme dark.ink="#ffffff"/>', "scheme.ink: the dark half's is #ffffff; write light.ink"),
    ('<colorScheme name="cobalt" light.muted="#ffffff"/>', 'scheme.muted: one value for both halves'),
    ('<colorScheme name="cobalt" dark.mode="dark"/>', 'scheme.mode: one value for both halves'),
    ('<colorScheme name="cobalt" sel.top="#111111" dark.sel.top="#222222"/>', 'scheme.sel.top: stated twice (sel.top and dark.sel.top)'),
    ('<view name="list"><element name="selection" light.painter="bar"/></view>', 'list.selection.painter: one value for both halves'),
    ('<view name="bar"><element name="battery" line="#111111" dark.line="#222222"/></view>', 'bar.battery.line: stated twice (line and dark.line)'),
])
def test_resolve_rejects_halves(body, message):
    assert error(resolved, body) == f't.xml:1: {message}'


def test_emit_mode_and_undeclared(tmp_path):
    inc = boot_inc(tmp_path, theme.builtins())
    # Minimal and Classic: one array for both halves, their light rows as before (no closing ' }': Task 13's
    # label and Task 17's bar follow on the line)
    assert '.light = minimal_schemes,' in inc and '.light = classic_schemes,' in inc and 'minimal_light' not in inc
    assert '.mode = SCHEME_DERIVE, .paper = 0xf4f4f4, .ink = 0x161616' in inc
    # Nano: its dark rows in nano_schemes, its light rows in nano_light
    dark, light = (inc.split(f'static const scheme_t nano_{a}[] = {{')[1].split('};')[0] for a in ('schemes', 'light'))
    assert '.paper = 0x000000, .ink = 0xffffff' in dark and '.paper = 0xffffff, .ink = 0x000000' in light
    assert '.light = nano_light,' in inc and '_Static_assert(sizeof nano_light == sizeof nano_schemes' in inc
    assert '.undeclared = (1 << SETTING_ROUNDED) | (1 << SETTING_NPCENTRED),' in inc
    assert inc.count('.undeclared = 0,') == 2


def test_derive_gloss():
    assert theme.derive_gloss([0x008ade] * 5) == [(p, f'#{c:06x}') for p, c in theme.GLOSS]  # the band's own body: itself
    for tones in theme.ACCENT_TONES:
        stops = theme.derive_gloss(tones)
        assert [p for p, _ in stops] == [0, 17, 38, 58, 79, 100] and stops[2][1] == f'#{tones[0]:06x}'
    assert all(h[1:3] == h[3:5] == h[5:7] for _, h in theme.derive_gloss(theme.ACCENT_TONES[0]))  # Graphite stays grey
    assert theme.derive_gloss(theme.ACCENT_TONES[4]) != [(p, f'#{c:06x}') for p, c in theme.GLOSS]  # Cobalt is near, not Nano Blue


def test_nano_blue_own_scheme():
    nano = theme.builtins()[2]
    blue = nano['schemes'][9]
    assert (blue['label'], blue['base'], blue['sel'], blue['sel_top'], blue['sel_edge']) == ('Nano Blue', 4, theme.GLOSS, theme.GLOSS_TOP, theme.GLOSS_EDGE)
    assert nano['accents'] == 0x37f and nano['opts'][1] == opt('SETTING_ACCENT', default=9, preset=9, values=0x37f)
    # each offered band is the same in both halves (Graphite's grey gloss is designed, not flipped)
    assert all(theme.scheme_half(s, True)['sel'] == theme.scheme_half(s, False)['sel'] for s in nano['schemes'])
    assert nano['schemes'][4]['sel'] == tuple((p, int(h[1:], 16)) for p, h in theme.derive_gloss(theme.ACCENT_TONES[4]))
    assert nano['schemes'][7]['sel'] == ()  # Ivory, not offered


@pytest.mark.parametrize('body, message', [
    ('<colorScheme name="sky" base="teal" label="Sky"/>', 'sky: unknown base teal'),
    ('<colorScheme name="sky" base="cobalt"/>', 'sky: an own scheme needs a label'),
    ('<colorScheme name="sky" base="cobalt" label=""/>', 'sky: an own scheme needs a label'),
    ('<colorScheme name="cobalt" base="cobalt" label="C"/>', 'cobalt: an ACCENTS row takes no base'),
    ('<colorSchemes accents="graphite" derive="matte"/>', 'colorSchemes.derive: unknown value matte'),
])
def test_own_scheme_rejects(body, message):
    assert error(resolved, body) == f't.xml:1: {message}'


def test_emit_own_scheme(tmp_path):
    inc = boot_inc(tmp_path, theme.builtins())
    assert '    { .tones = accents[4], ' in inc.split('static const scheme_t nano_schemes[] = {')[1].split('\n')[10]
    assert '.label = "Nano Blue" }' in inc and inc.count('.n_schemes = 9,') == 2 and '.n_schemes = 10,' in inc
    assert inc.count('/ sizeof *minimal_schemes >= ACCENT_N') == 1


def test_own_option():
    m, c, n = theme.builtins()
    row_art = own('ROWART', 'Row Art', ('Off', 'On'), ('nano-text', 'nano'), default=1, preset=1)
    assert n['opts'][-1] == row_art and not any(o.get('key') for o in m['opts'] + c['opts'])
    assert theme.geometry(n) == NANO and theme.geometry(n, {'ROWART': 'Off'}) == NANO_TEXT
    assert theme.geometry(n, {'ROWART': 'On'}) == NANO and theme.geometry(m, {'ROWART': 'Off'}) is SET0
    assert theme.theme_keys([m, c, n]) == {'NANO_ACCENT': (10, 9, None), 'ROWART': (2, 1, row_art)}
    assert {'ACCENT', 'MINIMAL_ACCENT', 'HOME', 'BATTERY', 'THEME', 'LIGHT', 'VIS'} <= theme.PAYLOAD_KEYS
    assert not {'ROWART', 'NANO_ACCENT'} & theme.PAYLOAD_KEYS and set(theme.ACCENT_KEYS) == {'ACCENT', 'MINIMAL_ACCENT'}
    assert not hasattr(theme, 'ROW_ART_OFF')


V = '<value label="Off" set="ipod"/><value label="On" set="ipod"/>'
@pytest.mark.parametrize('body, message', [
    ('<option key="ART" label="Art"><value label="Off" set="ipod"/></option>', 'ART: 2 to 10 values expected'),
    ('<option key="ART" label="Art"><value label="Off" set="ipod"/><value label="Off" set="ipod"/></option>', 'ART: duplicate value Off'),
    ('<option key="ART" label="Art"><value label="Off" set="flat"/><value label="On" set="ipod"/></option>', 'unknown geometry set "flat"'),
    ('<option key="ART" label="Art"><value label="Off"/><value label="On" set="ipod"/></option>', 'ART: value Off has no set'),
    ('<option key="ART" label="Art"><value label="Off" set="ipod" tint="1"/><value label="On" set="ipod"/></option>', 'unknown key value.tint'),
    (f'<option key="ART" label="Art" default="Dim">{V}</option>', 'ART: unknown value Dim'),
    (f'<option key="art" label="Art">{V}</option>', 'option.key: expected [A-Z][A-Z0-9_]*'),
    (f'<option key="LIGHT" label="Light">{V}</option>', "LIGHT: the payload's key"),
    (f'<option key="ART" label="">{V}</option>', 'ART: label expected 1..24 bytes, no control characters'),
    ('<option key="ART" label="Art"><value label="" set="ipod"/><value label="On" set="ipod"/></option>',
     'ART: value label expected 1..24 bytes, no control characters'),
    ('<option setting="home"><value label="Off" set="ipod"/></option>', 'home: a payload setting takes no <value>'),
    ('<option setting="home" label="H"/>', 'home: a payload setting takes no label'),
    (f'<option key="ART" label="Art" preset="Dim">{V}</option>', 'ART: unknown value Dim'),
    (f'<option key="ART" label="Art" keep="Dim">{V}</option>', 'ART: unknown value Dim'),
    (f'<option key="ART" label="Art">{V}<view name="bar"/></option>', 'unknown element <view>'),
])
def test_own_option_rejects(body, message):
    assert error(resolved, body) == f't.xml:1: {message}'


def test_builtins_rejects_a_shared_own_key(tmp_path):
    """Themes sharing an own key share its stored value, so they must declare the same values and default;
    each presets it as it likes (as Minimal and Classic preset LIGHT)."""
    shutil.copytree(THEMES, tmp_path, dirs_exist_ok=True, ignore=shutil.ignore_patterns('nano'))
    def put(folder, index, label, presets=''):
        (tmp_path/folder).mkdir(exist_ok=True)
        (tmp_path/folder/'theme.xml').write_text(
            f'<theme format="1" id="{folder}" name="{folder}" index="{index}" extends="classic">'
            f'<option setting="accent" key="ACCENT" default="graphite"/><option key="ART" label="{label}"{presets}>{V}</option></theme>')
    put('a', 2, 'Art', ' preset="On"'); put('b', 3, 'Art', ' preset="Off" keep="On"')
    assert list(theme.theme_keys(theme.builtins(tmp_path))) == ['ART']  # one key, one slot
    assert {t['id']: (t['opts'][-1]['preset'], t['opts'][-1]['keep']) for t in theme.builtins(tmp_path)[2:]} == {'a': (1, -1), 'b': (0, 1)}
    put('b', 3, 'Row Art')
    assert error(theme.builtins, tmp_path) == "b/theme.xml:1: ART is a's, with other values or default"


def test_emit_own_options(tmp_path):
    inc = boot_inc(tmp_path, theme.builtins())
    nano_opts = inc.split('static const option_t nano_opts[] = {\n')[1].split('};')[0].splitlines()
    assert nano_opts[-1] == '    { .setting = SETTING_OWN + 1, .def = 1, .preset = 1, .keep = -1, .values = 0x0 },'
    assert inc.count('.accent_slot = &st.own[0],') == 1 and 'nano_accent' not in inc
    assert ('const theme_key_t theme_keys[THEME_KEYS] = {\n'
            '    { .key = "NANO_ACCENT", .label = 0, .names = 0, .sets = 0, .n = 10, .def = 9 },\n'
            '    { .key = "ROWART", .label = "Row Art", .names = (const char *const[]){"Off", "On"}, '
            '.sets = (const unsigned char[]){GEOM_NANO_TEXT, GEOM_NANO}, .n = 2, .def = 1 },\n};') in inc
    assert inc.index('const theme_key_t theme_keys') > inc.index('enum { GEOM_IPOD, GEOM_NANO, GEOM_NANO_TEXT, GEOM_N };')


def test_nano_sets_from_its_theme_file():
    """Nano's sets are its theme.xml's: nano states every Geometry field (SET0's defaults never reach it),
    nano-text extends it. No tool names them."""
    m, c, n = theme.builtins()
    assert theme.sets(n) == {'ipod': SET0, 'nano': NANO, 'nano-text': NANO_TEXT} and theme.geometry(n) == NANO
    assert theme.sets(m) == theme.sets(c) == {'ipod': SET0} and (m['geoms'], c['geoms']) == ({}, {})
    decl = {e.attrs['name']: e.attrs for e in theme.load(THEMES/'nano'/'theme.xml').children
            if e.tag == 'geometry' and 'name' in e.attrs}
    assert set(decl['nano']) == {f.name for f in dataclasses.fields(Geometry)} and decl['nano-text']['extends'] == 'nano'
    assert not hasattr(ipod, 'NANO') and not hasattr(ipod, 'NANO_TEXT')


def test_geometry_declared():
    # An omitted field is SET0's, or its extends= set's; <geometry set> names a declared set; an extending
    # theme inherits its base's sets.
    t = resolved('<geometry name="pico" bar="26"/><geometry name="pico-2" extends="pico" text_x="20"/>'
                 '<geometry set="pico-2"/>')
    pico = dataclasses.replace(SET0, name='pico', bar=26)
    assert t['geoms'] == {'pico': pico, 'pico-2': dataclasses.replace(pico, name='pico-2', text_x=20)}
    assert theme.geometry(t) == t['geoms']['pico-2'] and theme.geometry_sets([t]) == [SET0, t['geoms']['pico-2']]
    u = resolved('', 'id="u" name="U" index="3" extends="nano"')
    assert theme.sets(u) == theme.sets(theme.builtins()[2]) and theme.geometry(u) == NANO


def test_geometry_ranges():
    """Every Geometry field has a range, so a task that adds a field adds one; SET0 and Nano's sets lie inside."""
    assert set(theme.GEOM_RANGES) == {f.name for f in dataclasses.fields(Geometry)} - {'name'}
    assert [theme.check_set(g) for g in (SET0, NANO, NANO_TEXT)] == [None, None, None]
    assert theme.check_set(dataclasses.replace(SET0, name='x', set_rows=5)) == 'geometry.set_rows: 5 outside 1..4'


@pytest.mark.parametrize('body, message', [
    ('<geometry name="Pico"/>', 'geometry.name: expected [a-z][a-z0-9-]*, 1..16 bytes, not n'),
    ('<geometry name="pico-text-and-more"/>', 'geometry.name: expected [a-z][a-z0-9-]*, 1..16 bytes, not n'),
    ('<geometry name="n"/>', 'geometry.name: expected [a-z][a-z0-9-]*, 1..16 bytes, not n'),
    ('<geometry name="ipod"/>', 'geometry set ipod declared twice'),
    ('<geometry name="pico"/><geometry name="pico"/>', 'geometry set pico declared twice'),
    ('<geometry name="pico" tint="1"/>', 'unknown key geometry.tint'),
    ('<geometry name="pico" set="ipod"/>', 'unknown key geometry.set'),
    ('<geometry name="pico" pitch="2x"/>', 'geometry.pitch: expected an integer'),
    ('<geometry name="pico" bar="24"/>', 'geometry.bar: 24 outside 26..40'),
    ('<geometry name="pico" pitch="28"/>', 'geometry.body: 68 outside 24..28'),
    ('<geometry name="pico" art_inset="9"/>', 'geometry.art_inset: 9 outside 0..8'),
    ('<geometry name="pico" pitch="28" body="28"/>', 'geometry.text_art: 52 outside 0..28'),
    ('<geometry name="pico" home_row="41"/>', 'geometry.home_row: 41 outside 28..40'),
    ('<geometry name="pico" extends="flat"/>', 'unknown geometry set "flat"'),
    ('<geometry set="pico"/><geometry name="pico" bar="26"/>', 'unknown geometry set "pico"'),
])
def test_geometry_declare_rejects(body, message):
    assert error(resolved, body) == f't.xml:1: {message}'


def test_builtins_rejects_a_shared_set_name(tmp_path):
    """A set name is one GEOM_* id and one <set>_<name>.bin prefix: the themes that declare it declare it alike."""
    shutil.copytree(THEMES, tmp_path, dirs_exist_ok=True, ignore=shutil.ignore_patterns('nano'))
    def put(folder, index, bar):
        (tmp_path/folder).mkdir(exist_ok=True)
        (tmp_path/folder/'theme.xml').write_text(
            f'<theme format="1" id="{folder}" name="{folder}" index="{index}" extends="classic">'
            f'<geometry name="pico" bar="{bar}"/><geometry set="pico"/>'
            '<option setting="accent" key="ACCENT" default="graphite"/></theme>')
    put('a', 2, 26); put('b', 3, 26)
    assert [theme.geometry(t).name for t in theme.builtins(tmp_path)[2:]] == ['pico', 'pico']
    put('b', 3, 28)
    assert error(theme.builtins, tmp_path) == "b/theme.xml:1: geometry set pico is a's, with other values"


def test_nano_from_minimal(tmp_path):
    """Nano is data: written out as a theme over Minimal that states every field, it builds to Nano."""
    shutil.copytree(THEMES, tmp_path, dirs_exist_ok=True)
    nano = theme.builtins()[2]
    (tmp_path / 'clone').mkdir()
    (tmp_path / 'clone' / 'theme.xml').write_text(
        theme.dump(nano, id='clone', name='Nano Clone', index='3', extends='minimal'))
    clone = theme.builtins(tmp_path)[3]
    skip = {'id', 'name', 'index', 'dev', 'line'}
    assert clone['id'] == 'clone'
    assert {k: v for k, v in clone.items() if k not in skip} == {k: v for k, v in nano.items() if k not in skip}


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
    assert all(s in inc for s in geom_vals_text([SET0, NANO, NANO_TEXT]))
    assert ipod.GEOM_VALS[:3] == ('bar', 'set_row', 'set_icon')
    assert [[getattr(g, n) for n in ipod.GEOM_VALS[:3]] for g in (SET0, NANO)] == [[30, 68, 40], [26, 28, 0]]


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
    assert (n['id'], n['name'], n['index'], n['dev'], n['geom_set'], n['accent_key'], n['accents']) == ('nano', 'Nano', 2, 1, 'nano', 'NANO_ACCENT', 0x37f)
    assert n['opts'] == [opt('SETTING_LIGHT', preset=1, keep=2), opt('SETTING_ACCENT', default=9, preset=9, values=0x37f),
                         opt('SETTING_HOME', default=0, preset=0), opt('SETTING_SHORTCUT'), opt('SETTING_BATTERY'), opt('SETTING_TIMETITLE', preset=1),
                         own('ROWART', 'Row Art', ('Off', 'On'), ('nano-text', 'nano'), default=1, preset=1)]
    assert theme.geometry_sets([m, c, n]) == [SET0, NANO, NANO_TEXT]
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
    assert re.findall(r'\.geom = (\d+)', inc) == ['0', '0', '1'] and '.accent_slot = &st.own[0],' in inc
