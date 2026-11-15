"""tools/theme.py: the Q2 theme XML reader, the schema and the generated theme table."""
import math
import dataclasses
import itertools
import re
import shutil
import xml.etree.ElementTree as ET

import pytest

import ipod
import theme
from ipod import Geometry, O, SET0
from theme import ThemeError

CLASSIC_LIGHT, CLASSIC_LIGHT_TEXT = (theme.sets(theme.builtins()[1])[s] for s in ('classic-light', 'classic-light-text'))  # Classic's sets: its theme.xml


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
        'sel': (), 'sel_top': 0, 'sel_edge': 0, 'sel_tall_edge': 0, 'sel_tall_line': 0, 'sel_tall_top': 0, 'sel_text': 0, 'bar': (), 'bar_line': 0, 'bar_line2': 0, 'mode': 0, 'paper': 0xf4f4f4, 'ink': 0x161616, 'light': {}}
MINIMAL_ROLES = {**ZERO, 'fill': 0xeeeeec, 'chevron': 0x777777, 'muted': 0x8c8c8c}
MODERN_ROLES = {**ZERO, 'bar_light': 0xdedede}


def opt(setting, default=-1, preset=-1, keep=-1, values=0):
    return {'setting': setting, 'def': default, 'preset': preset, 'keep': keep, 'values': values}


def own(key, label, names, sets, default=-1, preset=-1, keep=-1):  # a theme's own option, as resolve() keeps it
    return {**opt('SETTING_OWN', default, preset, keep), 'key': key, 'label': label, 'names': list(names), 'sets': sets and list(sets)}
HOMELIB, ROWPREVIEW = (own(k, label, ('Off', 'On'), None, default=1, preset=1) for k, label in (('HOMELIB', 'Library on Home'), ('ROWPREVIEW', 'Row Preview')))
STATUSBAR = own('STATUSBAR', 'Status Bar', ('Theme', 'Minimal', 'Modern', 'Classic'), None, default=0, preset=0)
SHOWQ2POD = own('SHOWQ2POD', 'Show Wordmark', ('Off', 'On'), None, default=1)


CLASSIC_THUMB = dict(thumb=1, ticks_turn=36, ticks_album=7, fast_at=23, fast_full=90, top_speed=20, track=30, snap=25, spin=130, far_left=250,
                     detent=80, hold_ms=5000, stop_ms=60, stop_left=60, rate_up=120, rate_down=50, back=50, dead=1, contact_ms=40, aim_snap=30,
                     lead_from=25, lead_full=100, lead_ms=45, row_shift=20, row_from=100, row_full=300, row_shrink=8, row_flat=200, row_out=33,
                     row_edge=160, row_width=40, row_key=72, jump_ms=700, jump_ticks=5, jump_at=32, jump_pace=140,
                     big_jump_at=75, big_jump_ms=240, enter_albums=8, slide_ms=160, fold_near=50,
                     arch_at=8, arch_full=16, arch_span=500, arch_in=100, arch_out=62, arch_tau=56)


CF_TODAY = {'cf.top': 13, 'cf.x1': 128, 'cf.xs': 35, 'cf.z1': 77, 'cf.zs': 0, 'cf.eye': 256, 'cf.bright1': 192,
            'cf.bright2': 128, 'cf.reflect': 46, 'cf.reflect_top': 90, 'cf.stride': 110, 'cf.radius': 12,
            'cf.tilt': 90, 'cf.horizon': 64, 'cf.third': 1, 'cf.swing': 2, 'cf.lift': 0, 'cf.eye_turn': 0, 'cf.view_h': 220, **{f'cf.{a}': v for a, v in CLASSIC_THUMB.items()}, 'cf.name_px': 24, 'cf.artist_px': 20, 'cf.text_y': 212, 'cf.artist_y': 32, 'cf.art_persp': 100}


MOTION_TODAY = {**{f'motion.{k}.{a}': 0 for k in ('page', 'playing', 'coverflow') for a in ('ms', 'easing', 'anim')},
                **{f'motion.{k}.{a}': 0 for k in ('inplace', 'swap', 'step', 'step_fast', 'coverflow_step', 'coverflow_step_fast') for a in ('ms', 'easing')},
                'motion.page.ms': 120, 'motion.page.easing': 5, 'motion.swap.ms': 120, 'motion.swap.easing': 8,
                'motion.step.ms': 200, 'motion.step.easing': 8, 'motion.step_fast.ms': 120, 'motion.step_fast.easing': 8,
                'motion.coverflow_step.ms': 200, 'motion.coverflow_step.easing': 8, 'motion.coverflow_step_fast.ms': 120, 'motion.coverflow_step_fast.easing': 8,
                'recall_ms': 300, 'bump_px': 6}


MINIMAL = {
    **MOTION_TODAY,
    'page_row': 48, 'page_detail': 64, 'page_title_h': 48, 'page_x': 36, 'page_cap_y': 4, 'page_cap_h': 32, 'page_det_y': 36, 'page_det_h': 24,
    'id': 'minimal', 'index': 0, 'name': 'Minimal', 'accents': 0x1ff, 'accent_key': 'MINIMAL_ACCENT',
    'painter[PAINT_SELECTION]': 0, 'painter[PAINT_BAR]': 0, 'painter[PAINT_BATTERY]': 0, 'chevron_home': 0, 'chevron_sel': 0, 'caps_mark': 1, 'caps_home': 1, 'caps_np': 1,
    'icons_mono': 1, 'icons_badge_muted': 1, 'icons_addon_mono': 1, 'icons_badge_ink': 0, 'art_final': 0, 'font[ROLE_NP]': 0, 'font[ROLE_INFO]': 0, 'font_px[ROLE_INFO]': 0, 'np_info_value': 0, 'np_album_op': 0, 'np_state_op': 0,
    'home_top': 8, 'home_full_row': 375, 'home_text_x': 39, 'home_label_end[0]': 8, 'home_label_end[1]': 33,
    'home_pane': (), 'pane_shadow': 0, 'pane_shadow_w': 0, 'pane_shadow_a': (), 'pane_light_a': 0, 'pane_icon_x': 0, 'pane_icon_y': 0, 'pane_note_x': 0, 'pane_note_y': 0, 'pane_count_y': 0, 'pane_count_w': 0, 'pane_count_px': 0, 'pane_svc_x': 0, 'pane_svc_y': 0, 'pane_svc_px': 0,
    'set_top': 8, 'set_text': 0, 'painter[PAINT_SCRUB]': 0, 'painter[PAINT_VOLUME]': 0, 'painter[PAINT_CONFIRM]': 0, 'painter[PAINT_PAGES]': 0, 'np_rows[0]': (), 'np_rows[1]': (), 'np_rows[2]': (), 'np_rows[3]': (), 'np_bar': 4, 'np_scrub': 8, 'np_track': (), 'np_fill': (), 'font[ROLE_TITLE]': 0, 'font[ROLE_META]': 0, 'font_px[ROLE_TITLE]': 0, 'font_px[ROLE_META]': 0, 'font_px[ROLE_NP]': 0, 'font[ROLE_HOME]': 0, 'font_px[ROLE_HOME]': 0, 'font[ROLE_BAR]': 0, 'font_px[ROLE_BAR]': 0, 'art_square': 0, 'art_x': 0, 'art_text_x': 0, 'art_text_end': 0, 'art_placeholder': 0, 'row_text_end': 0, 'sep': (), 'font[ROLE_TALL]': 0, 'font_px[ROLE_TALL]': 0, 'chevron_x': 0, 'home_chevron_x': 0, 'letter_raw': 0, 'letter_px': 0, 'batt': (), 'batt_empty': (), 'batt_low': 0, 'batt_low_w': 0, 'batt_line': 0, 'batt_edge': 0, 'batt_corner_a': 0, 'batt_foot_a': 0, 'batt_shade_a': 0, 'batt_soft_a': 0, 'batt_soft_top_a': 0, 'batt_emboss_a': 0, 'batt_emboss_nub_a': 0, 'bar_bt_dy': 0, 'bar_wifi_dy': 0, 'sb_w': 0, 'sb_right': 0, 'sb_top': 0, 'sb_bottom': 0, 'sb_min': 0, 'sb_thumb': 0, 'bolt': 0, 'bolt_halo_a': 0, 'state_stops': (), 'state_edge': (), 'state_halo_light': 0, 'state_halo_dark': 0, 'station_stops': (), 'station_mast': (), 'station_band_a': 0, 'station_shadow_a': 0, 'light': {}, 'np_layout': 0, 'geom_set': 'ipod', 'geoms': {},
    'schemes': [{**MINIMAL_ROLES, 'raw_red': int(v == 1), 'card': int(v == 7)} for v in range(9)],
    'opts': [STATUSBAR, opt('SETTING_TIMETITLE', preset=1), SHOWQ2POD, opt('SETTING_LIGHT', preset=0, keep=2),
             opt('SETTING_ACCENT', default=0, preset=0), opt('SETTING_ROUNDED', preset=1), opt('SETTING_HOME', preset=0),
             opt('SETTING_SHORTCUT'), opt('SETTING_BATTERY'), opt('SETTING_NPCENTRED')], 'undeclared': [], **CF_TODAY}
# Modern: the hand-written table's Classic, before Classic took Classic Light's look.
MODERN = {
    **MINIMAL, 'id': 'modern', 'index': 2, 'name': 'Modern', 'accents': 0x17f, 'accent_key': 'ACCENT',
    'painter[PAINT_SELECTION]': 2, 'chevron_home': 1, 'caps_mark': 0, 'caps_home': 0, 'caps_np': 0,
    'icons_mono': 0, 'icons_badge_muted': 0, 'icons_addon_mono': 0, 'font[ROLE_NP]': 1, 'np_album_op': 255, 'np_state_op': 255,
    'home_top': 0, 'home_full_row': 369, 'home_text_x': 33, 'home_label_end[0]': 33, 'home_label_end[1]': 33,
    'set_top': 0, 'np_bar': 8, 'np_scrub': 8, 'art_square': 1,
    'schemes': [ZERO if v == 7 else {**MODERN_ROLES, 'raw_red': int(v == 1)} for v in range(9)],
    'opts': [STATUSBAR, opt('SETTING_TIMETITLE', preset=0), SHOWQ2POD, opt('SETTING_LIGHT', preset=1, keep=2),
             opt('SETTING_ACCENT', default=0, preset=4, values=0x17f), opt('SETTING_ROUNDED', preset=0), opt('SETTING_HOME', preset=0),
             opt('SETTING_SHORTCUT'), opt('SETTING_BATTERY'), opt('SETTING_NPCENTRED')], 'undeclared': []}


def bare(t):
    return {k: v for k, v in t.items() if k != 'line'}


def resolved(body, attrs='id="t" name="T" index="2" extends="minimal"'):
    by_id = {n.attrs['id']: n for n in map(theme.load, sorted(THEMES.glob('*/theme.xml')))}
    root = theme.parse(f'<theme format="1" {attrs}>{body}</theme>', 't.xml')
    return theme.resolve(root, {**by_id, root.attrs.get('id', ''): root})


def set0_builtins():
    """Minimal and Modern: the engine's tests of two themes on the built-in set."""
    b = theme.builtins()
    return [b[0], b[2]]


def set0_themes(tmp_path):
    """Minimal and Modern in tmp_path, Modern at index 1 (no Classic between them)."""
    shutil.copytree(THEMES, tmp_path, dirs_exist_ok=True, ignore=shutil.ignore_patterns('classic-light'))
    p = tmp_path/'modern'/'theme.xml'
    p.write_text(p.read_text().replace('index="2"', 'index="1"'))


def test_accent_names():
    assert theme.ACCENT_NAMES == ['graphite', 'crimson', 'tidal', 'champagne', 'cobalt', 'tangerine', 'plum', 'ivory', 'green']
    assert theme.ACCENT_NAMES[theme.O['CRIMSON']] == 'crimson'
    assert theme.ACCENT_NAMES[theme.O['MINIMAL_ACCENT']] == 'ivory'


def test_frozen_table():
    """Minimal and Modern (the old Classic), as the hand-written table had them."""
    assert [bare(t) for t in set0_builtins()] == [MINIMAL, MODERN]


def test_extends():
    assert bare(resolved('', 'id="t" name="T" index="2" extends="modern"')) == {**MODERN, 'id': 't', 'name': 'T', 'index': 2}
    assert resolved('<option setting="accent" key="ACCENT"/>', 'id="t" name="T" index="2" extends="modern"')['opts'] == [opt('SETTING_ACCENT', values=0x17f)]
    t = resolved('<colorSchemes accents="cobalt green"/><option setting="accent" key="ACCENT" default="cobalt"/>',
                 'id="t" name="T" index="2" extends="modern"')
    assert (t['accents'], t['schemes'][0], t['schemes'][4], t['opts']) == (0x110, ZERO, MODERN_ROLES, [opt('SETTING_ACCENT', default=4, values=0x110)])
    # resolve() takes it, but builtins() rejects it beside Modern, whose ACCENT takes other colours
    assert resolved('<view name="home"><element name="rows" top="12"/></view>')['home_top'] == 12  # a card theme states numbers
    assert resolved('<colorScheme name="graphite" fill="#112233"/>')['schemes'][0]['fill'] == 0x112233
    assert resolved('<colorScheme name="graphite" fill="#AbCdEf"/>')['schemes'][0]['fill'] == 0xabcdef
    assert resolved('<colorSchemes/>')['accents'] == 0
    assert resolved('<view name="playing"><element name="album" opacity="255"/></view>')['np_album_op'] == 255


M = 'id="minimal" name="M" index="0"'
@pytest.mark.parametrize('body, attrs, message', [
    ('<font name="x"/>', None, 'unknown element <font>'),
    ('<view name="list"><element name="selection" painter="card"><element name="x"/></element></view>', None, 'unknown element <element>'),
    ('<view name="modal"/>', None, 'unknown view modal'),
    ('<view name="list" layout="x"/>', None, 'unknown key view.layout'),
    ('<view name="list"><element name="tile"/></view>', None, 'unknown element list.tile'),
    ('<view name="list"><element name="icons" grey="1"/></view>', None, 'unknown key list.icons.grey'),
    ('<view name="list"><element name="selection" painter="glass"/></view>', None, 'list.selection.painter: unknown value glass'),
    ('<view name="playing"><element name="title" font="serif"/></view>', None, 'playing.title.font: unknown value serif'),
    ('<colorScheme name="classic-blue"/>', None, 'unknown scheme classic-blue'),
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
    ('', 'id="T" name="T" index="2"', 'id: expected [a-z][a-z0-9_-]*'),
    ('', 'id="t" name="" index="2"', 'name: expected 1..48 bytes, no control characters'),
    ('', 'id="t" name="T" index="10"', 'index: expected a digit'),
    ('', 'id="t" name="T" index="2" colour="x"', 'unknown key theme.colour'),
    ('', 'name="T" index="2"', 'id: expected [a-z][a-z0-9_-]*'),
    ('', 'id="t" name="T"', 'index: expected a digit'),
    ('<colorScheme name=""/>', None, 'unknown scheme '),
    ('<option/>', None, 'unknown setting '),
    ('<view/>', None, 'unknown view '),
    ('<view name="list"><element/></view>', None, 'unknown element list.'),
    ('<view name="home"><element name="rows" top="8"/></view>', M, 'home.rows.top comes from patch/offsets.inc'),
    ('<colorScheme name="graphite" fill="#eeeeec"/>', M, 'scheme.fill comes from patch/offsets.inc'),
    ('<colorScheme name="graphite" sel.top="#000000"/>', None, 'scheme.sel.top: #000000 reads as unset; use #010101'),
    ('<colorScheme name="graphite" light.sel.text="#000000"/>', None, 'scheme.sel.text: #000000 reads as unset; use #010101'),
    ('<view name="bar"><element name="battery" line="#000000"/></view>', None, 'bar.battery.line: #000000 reads as unset; use #010101'),
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
C = '<colorSchemes accents="cobalt"/><option setting="accent" key="ACCENT" default="cobalt" preset="cobalt"/>'
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
    (A + '<colorScheme name="sky" base="cobalt" label="Sky"/><colorScheme name="sea" base="tidal" label="Sea"/>', None,
     '11 accents, at most 10'),
    (A + '<option setting="battery" default="percent"/>', None,
     "SETTING_BATTERY: default has no effect without values (settings_read holds a stock setting's default)"),
    ('<colorSchemes accents="cobalt"/>' + A, None, 'accent: slot 0 is not offered, so a missing key needs a default'),
    ('<view name="list"><element name="selection" painter="stops"/></view>', None,
     "selection: painter stops needs every offered scheme's sel.stops"),
    (C.replace('"cobalt"/>', '"cobalt green"/>', 1) + '<colorScheme name="cobalt" sel.stops="#08a6e7 0, #0065d6 100"/>'
     '<view name="list"><element name="selection" painter="stops"/></view>', None, "selection: painter stops needs every offered scheme's sel.stops"),
    ('<view name="bar"><element name="battery" painter="classic-light"/></view>', None, 'battery: this painter needs stops'),
    (C + '<view name="pages"><element name="own" art_final="1"/></view>', None, "pages.own: art_final is for classic-light's own images only"),
])
def test_validate_rejects(body, attrs, message):
    t = resolved(body, *([attrs] if attrs else []))
    assert error(theme.validate, t, 't.xml') == f't.xml:1: {message}'


def test_validate_accepts():
    t = resolved(C)
    assert theme.validate(t, 't.xml') is None


@pytest.mark.parametrize('body', [
    '<colorScheme name="cobalt" sel.stops="#08a6e7 0, #0065d6 100"/><view name="list"><element name="selection" painter="stops"/></view>',
    # one half's stops serve both: half() flips them for the other
    '<colorScheme name="cobalt" light.sel.stops="#08a6e7 0, #0065d6 100"/><view name="list"><element name="selection" painter="stops"/></view>',
    '<view name="bar"><element name="battery" painter="classic-light" stops="#90b27d 0, #539933 100"/></view>',
])
def test_validate_accepts_a_painter_with_its_stops(body):
    assert theme.validate(resolved(C + body), 't.xml') is None


def test_key():
    assert theme.key('a.b.c', 'f', 'bool') == theme.Key('a.b.c', 'f', 'bool', 0, 1, 0, 'live')
    assert theme.key('a.b.c', 'f', 'color').hi == 0xffffff
    assert theme.key('list.selection.painter', 'f', 'enum').hi == 2
    assert theme.key('a.b.c', 'f', 'int', 255).hi == 255


# Each coverflow.* key at its low end, but the eye, which must clear a side cover's near edge: a theme with no base states them all.
CF_LO = '<view name="coverflow">' + ''.join(
    f'<element name="{e}" ' + ' '.join(f'{k.path.rpartition(".")[2]}="{41 if k.path.endswith(".eye") else k.lo}"' for k in theme.SCHEMA.values()
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
    set0_themes(tmp_path)
    def put(folder, attrs, body):
        (tmp_path/folder).mkdir(exist_ok=True)
        (tmp_path/folder/'theme.xml').write_text(f'<theme format="1" {attrs}>{body}</theme>')
    shared = 'id="t" name="T" index="2" extends="modern"'
    put('t', shared, '<colorSchemes accents="cobalt green"/><option setting="accent" key="ACCENT" default="cobalt"/>')
    assert error(theme.builtins, tmp_path) == 't/theme.xml:1: accent key ACCENT is modern\'s, with other colorSchemes or default'
    put('t', shared, '')
    assert [t['id'] for t in theme.builtins(tmp_path)] == ['minimal', 'modern', 't']  # Modern's own: allowed
    put('t', shared, '<option setting="accent" key="ACCENT" default="tidal"/>')
    assert error(theme.builtins, tmp_path) == 't/theme.xml:1: accent key ACCENT is modern\'s, with other colorSchemes or default'


def test_builtins_rejects_other_options_on_a_set(tmp_path):
    """Themes on one set switch live, relabelling the booted theme's Appearance rows, so they declare the same options."""
    set0_themes(tmp_path)
    (tmp_path/'t').mkdir()
    (tmp_path/'t'/'theme.xml').write_text('<theme format="1" id="t" name="T" index="2" extends="modern">'
                                          '<option setting="accent" key="ACCENT" default="graphite"/></theme>')
    assert error(theme.builtins, tmp_path) == "t/theme.xml:1: t runs on minimal's set, with other options"


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


@pytest.mark.parametrize('alphas, message', [
    (' '.join(['1'] * 33), 'expected up to 32 alphas'),  # past theme_t's array
    ('256', 'expected up to 32 alphas'),  # past an alpha
    ('', 'expected up to 32 alphas'),
    ('1,2', 'expected up to 32 alphas'),
])
def test_alphas_rejects(alphas, message):
    with pytest.raises(theme.ThemeError, match=message):
        resolved(f'<view name="home"><element name="pane" shadow_a="{alphas}"/></view>')


def test_the_ipod_now_playing_and_its_bar_come_together():
    classic = theme.builtins()[1]
    with pytest.raises(theme.ThemeError, match="np_ipod needs playing.scrubber.stops"):
        theme.validate({**classic, 'np_track': (), 'light': {**classic['light'], 'np_track': ()}}, 't.xml')
    for stated in ({'np_track': classic['np_track']}, {'light': {'np_track': classic['light']['np_track']}}):
        with pytest.raises(theme.ThemeError, match="np_ipod needs playing.scrubber.stops"):
            theme.validate({**theme.builtins()[0], **stated}, 't.xml')
    # stated in one half alone: half() flips it for the other
    for half in ({'np_track': ()}, {'light': {**classic['light'], 'np_track': ()}}):
        theme.validate({**classic, **half}, 't.xml')


def test_a_shadow_past_its_alphas_is_an_error():
    t = {**theme.builtins()[0], 'pane_shadow_w': 3, 'pane_shadow_a': (9, 8)}
    with pytest.raises(theme.ThemeError, match=r'home.pane.shadow_w: 3 past its 2 alphas'):
        theme.validate(t, 't.xml')
    theme.validate({**t, 'pane_shadow_a': (9, 8, 7)}, 't.xml')


def test_classic_light_battery_offers_icon_percent_and_inside():
    # icon (0) never gates; Percent Inside (3) is exempt under BATT_CLASSIC_LIGHT; Percent (1) at the bar's 21 px, 37 px
    # for 100%, with Wi-Fi, the play state and Bluetooth still ends before Classic's centred clock (91 px of 93), and
    # bar_sync's gate, which takes the width the face draws, drops it to the icon only beside a wide codec badge
    # (test_nav_playing.py test_classics_percent_shows_beside_bluetooth_wifi_and_the_play_state)
    n = theme.builtins()[1]
    assert next(o['values'] for o in n['opts'] if o.get('setting') == 'SETTING_BATTERY') == 0b1011


def test_hyphen_in_an_id_is_a_c_name():
    t = resolved('', 'id="a-b" name="T" index="2" extends="minimal"')
    assert t['id'] == 'a-b' and theme.c_name(t) == 'a_b'


def test_home_rows_font_is_the_home_role():
    t = resolved('<view name="home"><element name="rows" font="classic-light" font_px="27"/></view>')
    assert (t['font[ROLE_HOME]'], t['font_px[ROLE_HOME]'], t['font[ROLE_NP]'], t['font_px[ROLE_NP]']) == (2, 27, 0, 0)


def test_resolve_takes_a_long_name():
    assert resolved('', f'id="t" name="{"é" * 24}" index="2" extends="minimal"')['name'] == 'é' * 24


MINIMAL_FIELDS = [('name', '"Minimal"'), ('painter[PAINT_SELECTION]', 'SEL_CARD'), ('painter[PAINT_BAR]', 'BAR_FLAT'), ('painter[PAINT_BATTERY]', 'BATT_STOCK'), ('chevron_home', 0), ('chevron_sel', 0), ('art_x', 0), ('art_text_x', 0), ('art_text_end', 0), ('art_placeholder', 0), ('row_text_end', 0), ('sep', '{{0}, {0}}'), ('font[ROLE_TALL]', 'FONT_DEFAULT'), ('font_px[ROLE_TALL]', 0), ('chevron_x', 0), ('home_chevron_x', 0), ('letter_raw', 0), ('letter_px', 0), ('caps_mark', 1),
                  ('caps_home', 1), ('caps_np', 1), ('icons_mono', 1), ('icons_badge_muted', 1), ('icons_addon_mono', 1), ('icons_badge_ink', 0),
                  ('font[ROLE_NP]', 'FONT_DEFAULT'), ('font_px[ROLE_NP]', 0), ('font[ROLE_INFO]', 'FONT_DEFAULT'), ('font_px[ROLE_INFO]', 0), ('np_info_value', '{0, 0}'), ('np_album_op', 0), ('np_state_op', 0), ('home_top', 8), ('home_full_row', 375),
                  ('home_text_x', 39), ('home_label_end[0]', 8), ('home_label_end[1]', 33), ('font[ROLE_HOME]', 'FONT_DEFAULT'), ('font_px[ROLE_HOME]', 0), ('home_pane', '{{0}, {0}}'), ('pane_shadow', 0), ('pane_shadow_w', 0), ('pane_shadow_a', '{0}'), ('pane_light_a', 0), ('pane_icon_x', 0), ('pane_icon_y', 0), ('pane_note_x', 0), ('pane_note_y', 0), ('pane_count_y', 0), ('pane_count_w', 0), ('pane_count_px', 0), ('pane_svc_x', 0), ('pane_svc_y', 0), ('pane_svc_px', 0), ('set_top', 8), ('set_text', 0), ('painter[PAINT_SCRUB]', 'SCRUB_STOCK'), ('painter[PAINT_VOLUME]', 'VOL_STOCK'), ('painter[PAINT_CONFIRM]', 'CONFIRM_STOCK'), ('painter[PAINT_PAGES]', 'PAGES_STOCK'), ('art_final', 0), ('np_rows[0]', '{0}'), ('np_rows[1]', '{0}'), ('np_rows[2]', '{0}'), ('np_rows[3]', '{0}'), ('np_bar', 4),
                  ('np_scrub', 8), ('np_track', '{{0}, {0}}'), ('np_fill', '{0}'), ('art_square', 0), ('font[ROLE_TITLE]', 'FONT_DEFAULT'), ('font[ROLE_META]', 'FONT_DEFAULT'),
                  ('font_px[ROLE_TITLE]', 0), ('font_px[ROLE_META]', 0), ('font[ROLE_BAR]', 'FONT_DEFAULT'), ('font_px[ROLE_BAR]', 0),
                  ('batt', '{0}'), ('batt_line', '{0, 0}'), ('batt_empty', '{0}'), ('batt_low', 0), ('batt_low_w', 0), ('batt_edge', 0), ('batt_corner_a', 0), ('batt_foot_a', 0), ('batt_shade_a', 0), ('batt_soft_a', 0), ('batt_soft_top_a', 0), ('batt_emboss_a', 0), ('batt_emboss_nub_a', 0), ('bar_bt_dy', 0), ('bar_wifi_dy', 0), ('sb_w', 0), ('sb_right', 0), ('sb_top', 0), ('sb_bottom', 0), ('sb_min', 0), ('sb_thumb', '{0, 0}'),
                  ('accent_slot', '&st.minimal_accent'), ('accent_key', '"MINIMAL_ACCENT"'),
                  ('schemes', 'minimal_schemes'), ('light', 'minimal_schemes'), ('n_schemes', 9), ('opts', 'minimal_opts'), ('n_opts', 10),
                  ('undeclared', 0), ('np_layout', 'NP_IPOD'),
                  *CF_TODAY.items(), ('page_row', 48), ('page_detail', 64), ('page_title_h', 48), ('page_x', 36), ('page_cap_y', 4), ('page_cap_h', 32), ('page_det_y', 36), ('page_det_h', 24), ('geom', 0),
                  *((f'motion[MOTION_{k}].hint', 0) for k in ('PAGE', 'PLAYING', 'COVERFLOW')),
                  *((f'motion[MOTION_{k}].{a}', v) for k, ms, e in (('INPLACE', 0, 0), ('SWAP', 120, 8), ('STEP', 200, 8), ('STEP_FAST', 120, 8), ('COVERFLOW_STEP', 200, 8), ('COVERFLOW_STEP_FAST', 120, 8))
                    for a, v in (('ms', ms), ('easing', e))),
                  ('recall_ms', 300), ('bump_px', 6)]


def test_emit(tmp_path):
    theme.emit(theme.builtins()[:1], tmp_path)
    flags = {1: 2, 7: 1}
    assert (tmp_path/'themes.inc').read_text().startswith('\n'.join([
        '/* tools/theme.py wrote this from assets/themes/<id>/theme.xml; edit those. */',
        'static const scheme_t minimal_schemes[] = {',
        *(f'    {{ .tones = accents[{v}], .flags = {flags.get(v, 0)}, .fill = 0xeeeeec, .chevron = 0x777777, '
          f'.muted = 0x8c8c8c, .bar_light = 0x000000, .sel = {{0}}, .sel_top = 0x000000, .sel_edge = 0x000000, .sel_tall_edge = 0x000000, .sel_tall_line = 0x000000, .sel_tall_top = 0x000000, '
          f'.sel_text = 0x000000, .mode = SCHEME_DERIVE, .paper = 0xf4f4f4, .ink = 0x161616, .bar = {{0}}, .bar_line = 0x000000, .bar_line2 = 0x000000 }},' for v in range(9)),
        '};',
        '_Static_assert(sizeof minimal_schemes / sizeof *minimal_schemes >= ACCENT_N, "one scheme per ACCENTS row, then the theme\'s own");',
        'static const option_t minimal_opts[] = {',
        '    { .setting = SETTING_OWN + 0, .def = 0, .preset = 0, .keep = -1, .values = 0x0 },',
        '    { .setting = SETTING_TIMETITLE, .def = -1, .preset = 1, .keep = -1, .values = 0x0 },',
        '    { .setting = SETTING_OWN + 1, .def = 1, .preset = -1, .keep = -1, .values = 0x0 },',
        '    { .setting = SETTING_LIGHT, .def = -1, .preset = 0, .keep = 2, .values = 0x0 },',
        '    { .setting = SETTING_ACCENT, .def = 0, .preset = 0, .keep = -1, .values = 0x0 },',
        '    { .setting = SETTING_ROUNDED, .def = -1, .preset = 1, .keep = -1, .values = 0x0 },',
        '    { .setting = SETTING_HOME, .def = -1, .preset = 0, .keep = -1, .values = 0x0 },',
        '    { .setting = SETTING_SHORTCUT, .def = -1, .preset = -1, .keep = -1, .values = 0x0 },',
        '    { .setting = SETTING_BATTERY, .def = -1, .preset = -1, .keep = -1, .values = 0x0 },',
        '    { .setting = SETTING_NPCENTRED, .def = -1, .preset = -1, .keep = -1, .values = 0x0 },',
        '};',
        'const theme_t themes[THEME_N] = {',
        '    {', *(f'        .{k} = {v},' for k, v in MINIMAL_FIELDS), '    },',
        '};', '']))


def test_emit_both(tmp_path):
    theme.emit(set0_builtins(), tmp_path)
    text = (tmp_path/'themes.inc').read_text()
    assert [l.split(' = ')[0] for l in text.splitlines() if l.startswith('static const')] == [
        'static const scheme_t minimal_schemes[]', 'static const option_t minimal_opts[]',
        'static const scheme_t modern_schemes[]', 'static const option_t modern_opts[]',
        'static const volatile short geom_vals[GEOM_N][V_N]']
    assert 'extern const theme_t themes[THEME_N];' in (tmp_path/'themes.h').read_text()  # navigation.c's picker
    assert ('    { .tones = accents[7], .flags = 0, .fill = 0x000000, .chevron = 0x000000, .muted = 0x000000, .bar_light = 0x000000, '
            '.sel = {0}, .sel_top = 0x000000, .sel_edge = 0x000000, .sel_tall_edge = 0x000000, .sel_tall_line = 0x000000, .sel_tall_top = 0x000000, .sel_text = 0x000000, .mode = SCHEME_DERIVE, .paper = 0xf4f4f4, '
            '.ink = 0x161616, .bar = {0}, .bar_line = 0x000000, .bar_line2 = 0x000000 },\n    { .tones = accents[8]') in text
    assert '    { .setting = SETTING_ACCENT, .def = 0, .preset = 4, .keep = -1, .values = 0x17f },' in text
    assert '        .accent_slot = &st.accent,\n        .accent_key = "ACCENT",\n        .schemes = modern_schemes,' in text
    assert '    },\n};\nconst volatile unsigned short boot_values' in text
    assert (tmp_path/'themes.h').read_text().startswith('/* tools/theme.py wrote this from assets/themes/<id>/theme.xml; edit those. */\nenum { GEOM_IPOD, GEOM_N };\n')


def test_playing_layout_builtins_ipod():
    assert [t['np_layout'] for t in theme.builtins()] == [0, 0, 0]


def test_playing_layout_resolves_centred():
    t = resolved('<view name="playing" layout="centred"/>'); theme.validate(t, 't.xml')
    assert t['np_layout'] == 1


def test_playing_layout_rejects_unknown():
    assert error(resolved, '<view name="playing" layout="other"/>') == 't.xml:1: playing.layout: unknown value other'


def test_emit_np_layout(tmp_path):
    theme.emit(theme.builtins(), tmp_path)
    assert (tmp_path/'themes.inc').read_text().count('.np_layout = NP_IPOD,') == 3


def test_view_attr():
    assert theme.view_attr('list.selection.painter') and theme.view_attr('home.rows.top')
    assert not theme.view_attr('playing.layout') and not theme.view_attr('scheme.fill')
    assert not theme.view_attr('scheme.sel.stops')  # a scheme role has two dots too


# Enum fields and value counts come out as C names, so a reordered or added value is a compile error.
def test_emit_names_enums_and_counts(tmp_path):
    inc = boot_inc(tmp_path, theme.builtins())
    assert [inc.count(f'.painter[PAINT_SELECTION] = {p},') for p in ('SEL_CARD', 'SEL_STOPS', 'SEL_BAR')] == [1, 1, 1]
    assert inc.count('.np_layout = NP_IPOD,') == 3 and '.np_layout = 0,' not in inc
    for name, (_, values) in theme.SETTINGS.items():
        assert f'#define SETTING_N_{name.upper()} {len(values)}\n' in inc


def with_role(monkeypatch, **cf):
    monkeypatch.setitem(theme.ROLE_MACROS, 'minimal', {**theme.ROLE_MACROS['minimal'], **{f'cf.{k}': str(v) for k, v in cf.items()}})


@pytest.mark.parametrize('field,value', [('eye', 2047), ('eye', 0), ('xs', 800), ('zs', 800), ('x1', 1024), ('reflect', 0),
    ('bright2', 0), ('text_y', 209), ('name_px', 33), ('artist_px', 29), ('radius', 84), ('stride', 0),
    ('tilt', 29), ('tilt', 91), ('horizon', 0), ('horizon', 100), ('third', 2), ('swing', 3), ('lift', 41), ('eye_turn', 401), ('view_h', 209), ('view_h', 295),
    ('thumb', 2), ('detent', 101), ('hold_ms', 30001), ('dead', 21), ('row_width', 101), ('art_persp', 101),
    ('jump_ms', 5001), ('jump_ticks', 201), ('jump_at', 1001), ('jump_pace', 2001), ('big_jump_at', 1001), ('big_jump_ms', 5001),
    ('enter_albums', 13), ('slide_ms', 1001), ('fold_near', 51)])
def test_coverflow_range_rejects(monkeypatch, field, value):
    if f'cf.{field}' not in theme.CF_ROLES:  # Minimal's theme.xml states the thumb and where the covers stand, not offsets.inc
        with pytest.raises(theme.ThemeError, match=rf'coverflow\.shelf\.{field}: {value} outside'):
            resolved(f'<view name="coverflow"><element name="shelf" {field}="{value}"/></view>')
        return
    with_role(monkeypatch, **{field: value})
    with pytest.raises(theme.ThemeError, match=rf'coverflow\.\w+\.{field}: {value} outside'):
        theme.builtins()


@pytest.mark.parametrize('field,value,message', [
    ('cf.top', 15, 'top \\+ 160 \\+ reflect = 221 exceeds 220'), ('cf.reflect', 48, 'top \\+ 160 \\+ reflect = 221 exceeds 220'),
    ('cf.view_h', 291, 'view_h 291 exceeds the client height 290'),
    ('cf.bright2', 193, 'bright2 193 exceeds bright1 192'), ('cf.text_y', 231, 'text_y \\+ 60 = 291 exceeds the client height 290'),
    ('cf.row_out', 29, 'row_out 29 puts 16 covers of the held row on the screen, past CF_WIDE 15')])
def test_coverflow_cross_rejects(field, value, message):
    with pytest.raises(theme.ThemeError, match=message):
        theme.validate({**theme.builtins()[0], field: value}, 't.xml')


def test_coverflow_eye_turn_stays_in_range():
    theme.validate({**theme.builtins()[0], 'cf.eye': 1646, 'cf.eye_turn': 400}, 't.xml')
    with pytest.raises(theme.ThemeError, match='eye \\+ eye_turn = 2047 exceeds 2046'):
        theme.validate({**theme.builtins()[0], 'cf.eye': 1647, 'cf.eye_turn': 400}, 't.xml')


def test_coverflow_thumb_is_every_themes():  # the page's numbers, all retunable: Classic's, in all three
    assert set(CLASSIC_THUMB) == set(theme.CF_THUMB)
    ts = theme.builtins()
    assert sorted(t['id'] for t in ts) == ['classic-light', 'minimal', 'modern']
    for t in ts:
        assert {a: t[f'cf.{a}'] for a in CLASSIC_THUMB} == CLASSIC_THUMB, t['id']


def test_coverflow_arch_needs_its_numbers():  # th_arch's divisors, and a span of an album at least
    c = theme.builtins()[0]
    for bad in ({'cf.arch_tau': 0}, {'cf.arch_in': 0}, {'cf.arch_out': 0}, {'cf.arch_span': 99}, {'cf.arch_full': c['cf.arch_at']}):
        with pytest.raises(theme.ThemeError, match='coverflow.shelf: arch_span'):
            theme.validate({**c, **bad}, 't.xml')
    theme.validate({**c, 'cf.arch_span': 0, 'cf.arch_tau': 0, 'cf.arch_full': 0}, 't.xml')  # no arch: the row at speed, none read


@pytest.mark.parametrize('field', ['ticks_turn', 'ticks_album', 'track', 'snap', 'spin', 'far_left', 'rate_up', 'rate_down', 'lead_ms', 'row_edge',
                                   'enter_albums', 'slide_ms'])
def test_coverflow_thumb_needs_its_divisors(field):
    c = next(t for t in theme.builtins() if t['id'] == 'classic-light')
    with pytest.raises(theme.ThemeError, match=f'coverflow.shelf: thumb needs {field}'):
        theme.validate({**c, f'cf.{field}': 0}, 't.xml')


@pytest.mark.parametrize('lo,hi', [('fast_at', 'fast_full'), ('fast_at', 'big_jump_at'), ('lead_from', 'lead_full'), ('row_from', 'row_full')])
def test_coverflow_thumb_ranges_run_upward(lo, hi):
    c = next(t for t in theme.builtins() if t['id'] == 'classic-light')
    with pytest.raises(theme.ThemeError, match=f'coverflow.shelf: {hi} {c[f"cf.{lo}"]} must pass {lo} {c[f"cf.{lo}"]}'):
        theme.validate({**c, f'cf.{hi}': c[f'cf.{lo}']}, 't.xml')
    theme.validate({**c, 'cf.thumb': 0, f'cf.{hi}': 0}, 't.xml')  # off, they're not read


def test_coverflow_shelf_near_edge_clears_the_eye():
    with pytest.raises(theme.ThemeError, match="eye \\+ z1 = 69 doesn't clear 80 sin\\(tilt\\) = 69\\.3$"):
        theme.validate({**theme.builtins()[0], 'cf.eye': 39, 'cf.z1': 30, 'cf.tilt': 60}, 't.xml')
    theme.validate({**theme.builtins()[0], 'cf.eye': 40, 'cf.z1': 30, 'cf.tilt': 60, 'cf.thumb': 0}, 't.xml')  # no held row to fit


def test_coverflow_swing_near_edge_clears_the_eye_mid_step():
    tight = {**theme.builtins()[0], 'cf.eye': 41, 'cf.z1': 0, 'cf.tilt': 30, 'cf.thumb': 0}  # rest clears by 1, mid-step it doesn't; no held row to fit
    theme.validate(tight, 't.xml')
    with pytest.raises(theme.ThemeError, match="lift / 4 = -39\\.0$"):
        theme.validate({**tight, 'cf.swing': 1}, 't.xml')
    theme.validate({**tight, 'cf.swing': 1, 'cf.eye': 86}, 't.xml')
    with pytest.raises(theme.ThemeError, match='lift / 4 = 0\\.0$'):
        theme.validate({**tight, 'cf.swing': 1, 'cf.eye': 85, 'cf.lift': 20}, 't.xml')
    with pytest.raises(theme.ThemeError, match='rests 10368 px out, past 2048$'):
        theme.validate({**theme.builtins()[0], 'cf.swing': 1, 'cf.eye': 81, 'cf.z1': 0, 'cf.tilt': 90}, 't.xml')


def test_coverflow_caption_follows_the_bar(monkeypatch):
    monkeypatch.setitem(theme.GEOMETRIES, 'tall', Geometry(name='tall', bar=34))
    t = {**theme.builtins()[0], 'geom_set': 'tall'}
    with pytest.raises(theme.ThemeError, match='text_y \\+ 60 = 287 exceeds the client height 286'):
        theme.validate({**t, 'cf.text_y': 227}, 't.xml')
    theme.validate({**t, 'cf.text_y': 226}, 't.xml')


def test_coverflow_edges_pass(monkeypatch):   # each limit itself is allowed
    for edge in ({'cf.top': 14}, {'cf.text_y': 230}, {'cf.bright2': 192}):
        theme.validate({**theme.builtins()[0], **edge}, 't.xml')
    theme.validate({**theme.builtins()[0], 'cf.eye': 2046, 'cf.x1': 1023, 'cf.xs': 0}, 't.xml')


def test_classic_light_shelf():  # its side covers' geometry from its keys; the frame holds the reflection and fits the page
    t = next(t for t in theme.builtins() if t['id'] == 'classic-light')
    eye, c, s = t['cf.eye'], math.cos(math.radians(t['cf.tilt'])), math.sin(math.radians(t['cf.tilt']))
    assert eye * c / s <= 3  # where a side cover's top and bottom edges meet, past the centre column
    def outer(n):
        z = eye + t['cf.z1'] + n * t['cf.zs'] - 80 * s
        return (t['cf.x1'] + n * t['cf.xs'] + 80 * c) * eye / z, 160 * eye / z
    for n, (x, h) in enumerate(((129.5, 162), (165, 162), (200.5, None))):
        assert abs(outer(n)[0] - x) <= 1 and (h is None or abs(outer(n)[1] - h) <= 1)
    assert (t['cf.top'] + t['geoms'][t['geom_set']].bar, t['cf.horizon'], t['cf.third']) == (58, 64, 1)
    assert (t['cf.swing'], t['cf.lift'], t['cf.eye_turn']) == (2, 0, 0)  # straight out, as #329's lab
    assert (t['cf.reflect'], t['cf.bright1'], t['cf.bright2']) == (46, 256, 256) and abs(t['cf.reflect_top'] * 100 / 256 - 35) < 1
    assert t['cf.top'] + 160 + t['cf.reflect'] <= t['cf.view_h'] <= 320 - t['geoms'][t['geom_set']].bar
    foot = t['cf.top'] + 160 * t['cf.horizon'] // 100 + outer(0)[1] * (100 - t['cf.horizon']) / 100
    assert foot + outer(0)[1] * t['cf.reflect'] / 160 <= t['cf.view_h']  # a side cover's reflection isn't cut either


def test_emit_cf(tmp_path):
    theme.emit(theme.builtins()[:1], tmp_path)
    text = (tmp_path/'themes.inc').read_text()
    assert text.count('.cf.eye = 256,') == 1 and text.count('.cf.text_y = 212,') == 1


# page_list's rows, detail rows and in-page title: 48/64/48 unless a theme sets them (open class).
def test_page_rows_defaults_and_classic_light():
    assert [(t['page_row'], t['page_detail'], t['page_title_h']) for t in theme.builtins()] == [(48, 64, 48), (32, 52, 0), (48, 64, 48)]
    assert [t['page_x'] for t in theme.builtins()] == [O['CF_EDGE'], 20, O['CF_EDGE']]
    # a detail row's boxes; a box holds its font at Classic's sizes (a label draws nothing in a shorter one)
    assert [(t['page_cap_y'], t['page_cap_h'], t['page_det_y'], t['page_det_h']) for t in theme.builtins()] == [(4, 32, 36, 24), (-1, 28, 28, 18), (4, 32, 36, 24)]
    cl = theme.builtins()[1]
    assert cl['page_cap_h'] >= cl['font_px[ROLE_TALL]'] and cl['page_det_h'] >= cl['font_px[ROLE_META]'] and cl['page_row'] >= cl['font_px[ROLE_TITLE]']
    t = resolved('<view name="page"><element name="rows" height="30" detail_height="56" detail_y="30" detail_h="24"/><element name="title" height="0"/></view>')
    theme.validate(t, 't.xml')
    assert (t['page_row'], t['page_detail'], t['page_title_h']) == (30, 56, 0)
    assert all(theme.SCHEMA[p].cls == 'open' for p in ('page.rows.height', 'page.rows.detail_height', 'page.title.height', 'page.rows.title_y'))


@pytest.mark.parametrize('body, message', [
    ('<view name="page"><element name="rows" height="19"/></view>', 't.xml:1: page.rows.height: 19 outside 20..64'),
    ('<view name="page"><element name="rows" detail_height="81"/></view>', 't.xml:1: page.rows.detail_height: 81 outside 20..80'),
    ('<view name="page"><element name="title" height="49"/></view>', 't.xml:1: page.title.height: 49 outside 0..48'),
    ('<view name="page"><element name="rows" detail_height="52" detail_y="36" detail_h="24"/></view>', 't.xml:1: page.rows: detail box ends at 60, past the 52 px row'),
    ('<view name="page"><element name="rows" detail_height="30" title_y="4" title_h="32" detail_y="16" detail_h="12"/></view>', 't.xml:1: page.rows: title box ends at 36, past the 30 px row'),
    ('<view name="page"><element name="rows" title_h="27"/></view><view name="list"><element name="row" tall_px="28"/></view>', 't.xml:1: page.rows.title_h: 27 is shorter than its 28 px font'),
    ('<view name="page"><element name="rows" detail_h="16"/></view><view name="list"><element name="row" meta_px="17"/></view>', 't.xml:1: page.rows.detail_h: 16 is shorter than its 17 px font'),
    ('<view name="page"><element name="rows" height="26"/></view><view name="list"><element name="row" title_px="27"/></view>', 't.xml:1: page.rows.height: 26 is shorter than its 27 px font'),
])
def test_page_rows_reject(body, message):
    assert error(lambda: theme.validate(resolved(body), 't.xml')) == message


def test_emit_page_rows(tmp_path):
    theme.emit(theme.builtins(), tmp_path)
    text = (tmp_path/'themes.inc').read_text()
    assert text.count('.page_row = 48,') == 2 and text.count('.page_detail = 52,') == 1 and text.count('.page_title_h = 0,') == 1 and text.count('.page_cap_y = -1,') == 1


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
    assert [theme.geometry(t) for t in set0_builtins()] == [SET0, SET0]
    assert theme.geometry_sets(set0_builtins()) == [SET0]
    assert minimal_with('<geometry set="ipod"/>') == theme.builtins()[0]


@pytest.mark.parametrize('geometry, message', [
    ('<geometry set="other"/>', 'unknown geometry set "other"'),
    ('<geometry set="ipod" bar="26"/>', 'unknown key geometry.bar')])
def test_geometry_rejects(geometry, message):
    with pytest.raises(theme.ThemeError, match=message):
        minimal_with(geometry)


def test_geometry_sets_set0_first(test_set):
    m, c = set0_builtins()
    assert theme.geometry_sets([dict(m, geom_set='test'), c]) == [SET0, TEST]
    assert theme.geometry_sets([dict(m, geom_set='test')]) == [SET0, TEST]


def test_geometry_sets_refuse_a_wide_value(monkeypatch):
    monkeypatch.setitem(theme.GEOMETRIES, 'wide', Geometry(name='wide', pitch=0x8000))
    with pytest.raises(ValueError, match='wide: pitch 32768 does not fit an immediate'):
        theme.geometry_sets([dict(theme.builtins()[0], geom_set='wide')])


def combinations(t):
    """Every value combination of t's options as {option: value}: a bound setting's names (narrowed by a values
    mask) under its SETTINGS name, an own option's labels under its key."""
    names = {macro: (name, v) for name, (macro, v) in theme.SETTINGS.items()}
    axes = [(o['key'], o['names']) if o.get('key') else names[o['setting']] for o in t['opts']]
    return [dict(zip([a for a, _ in axes], combo)) for combo in itertools.product(
        *([x for i, x in enumerate(v) if not o['values'] or o['values'] >> i & 1] for (_, v), o in zip(axes, t['opts'])))]




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
    ('#94c7de 0', '2 to 13 stops'),
    (', '.join(f'#000000 {p}' for p in (0, *range(10, 61, 5), 70, 100)), '2 to 13 stops'),
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
    # no closing ' }': later roles (the mode, the label) follow sel_text on the line
    assert ('.sel = {6, {0, 17, 38, 58, 79, 100}, {0x08a6e7, 0x009ade, 0x008ade, 0x007dde, 0x006dd6, '
            '0x0065d6}}, .sel_top = 0x94c7de, .sel_edge = 0x004184, .sel_tall_edge = 0x004aa3, .sel_tall_line = 0x688db3, .sel_tall_top = 0x27ade5, .sel_text = 0xffffff') in inc
    assert inc.count('.painter[PAINT_SELECTION] = SEL_STOPS,') == 1


def test_bar_painter_and_scheme_bar_roles():
    t = resolved('<colorScheme name="graphite" bar.stops="#ffffff 0, #9c9e9c 100" bar.line="#5a595a"/>'
                 '<view name="bar"><element name="fill" painter="gloss"/></view>')
    assert t['painter[PAINT_BAR]'] == 1 and t['schemes'][0]['bar'] == ((0, 0xffffff), (100, 0x9c9e9c))
    assert t['schemes'][0]['bar_line'] == 0x5a595a and t['schemes'][0]['bar_line2'] == 0
    classic_light = theme.builtins()[1]['schemes'][0]
    assert classic_light['bar_line2'] == 0x848584
    assert error(resolved, '<view name="bar"><element name="fill" painter="shiny"/></view>') == \
        't.xml:1: bar.fill.painter: unknown value shiny'


def test_bar_battery_painter_rejects():
    assert error(resolved, '<view name="bar"><element name="battery" painter="segments"/></view>') == \
        't.xml:1: bar.battery.painter: unknown value segments'


def test_theme_level_halves():
    classic_light = theme.builtins()[1]
    assert classic_light['batt_line'] == classic_light['light']['batt_line'] == 0x717070  # both halves stated, the same grey
    assert theme.half(classic_light, False, 0xffffff, 0)['batt_line'] == 0x717070
    assert theme.half({**classic_light, 'batt_line': 0}, False, 0xffffff, 0)['batt_line'] != 0  # one stated: the other derives
    assert error(resolved, '<view name="bar"><element name="battery" light.painter="classic-light"/></view>') == \
        't.xml:1: bar.battery.painter: one value for both halves'


def test_scheme_mode_paper_ink():
    t = resolved('<colorScheme mode="light" light.paper="#ffffff" light.ink="#000000"/>')
    assert {(s['mode'], s['paper'], s['ink']) for s in t['schemes']} == {(1, 0xffffff, 0)}
    minimal, classic, modern = theme.builtins()
    assert {(s['mode'], s['paper'], s['ink']) for s in minimal['schemes']} == {(0, 0xf4f4f4, 0x161616)}
    assert {(s['mode'], s['paper'], s['ink']) for s in classic['schemes'] if s['sel']} == {(0, 0xffffff, 0)}
    assert (minimal['undeclared'], set0_builtins()[1]['undeclared']) == ([], [])
    assert classic['undeclared'] == ['SETTING_ROUNDED', 'SETTING_NPCENTRED']
    assert classic['opts'][3] == opt('SETTING_LIGHT', preset=1, keep=2)
    assert not (theme.shared(classic) or not theme.shared(minimal) or not theme.shared(set0_builtins()[1]))


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


def test_a_hue_stated_in_one_half_is_the_others():
    # flip and its inverse turn neutrals only: a hue reads the same on either paper
    s = resolved('<colorScheme name="cobalt" light.sel.text="#94c7de" sel.edge="#94c7de"/>')['schemes'][4]
    assert theme.scheme_half(s, False)['sel_text'] == theme.scheme_half(s, True)['sel_edge'] == 0x94c7de
    assert theme.unflip(0x94c7de, 0xffffff, 0) == 0x94c7de


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
    ('<view name="list"><element name="selection" light.painter="card"/></view>', 'list.selection.painter: one value for both halves'),
    ('<view name="bar"><element name="battery" line="#111111" dark.line="#222222"/></view>', 'bar.battery.line: stated twice (line and dark.line)'),
])
def test_resolve_rejects_halves(body, message):
    assert error(resolved, body) == f't.xml:1: {message}'


def test_emit_mode_and_undeclared(tmp_path):
    inc = boot_inc(tmp_path, theme.builtins())
    # Minimal: one array for both halves, its light rows as before (no closing ' }': the
    # label and the bar follow on the line); Modern's the same
    assert '.light = minimal_schemes,' in inc and '.light = modern_schemes,' in inc and 'minimal_light' not in inc
    assert '.mode = SCHEME_DERIVE, .paper = 0xf4f4f4, .ink = 0x161616' in inc
    # Classic: its dark rows in classic_light_schemes, its light rows in classic_light_light
    dark, light = (inc.split(f'static const scheme_t classic_light_{a}[] = {{')[1].split('};')[0] for a in ('schemes', 'light'))
    assert '.paper = 0x000000, .ink = 0xffffff' in dark and '.paper = 0xffffff, .ink = 0x000000' in light
    assert '.light = classic_light_light,' in inc and '_Static_assert(sizeof classic_light_light == sizeof classic_light_schemes' in inc
    assert '.undeclared = (1 << SETTING_ROUNDED) | (1 << SETTING_NPCENTRED),' in inc
    assert inc.count('.undeclared = 0,') == 2


def test_derive_gloss():
    assert theme.derive_gloss([0x008ade] * 5) == [(p, f'#{c:06x}') for p, c in theme.GLOSS]  # the band's own body: itself
    for tones in theme.ACCENT_TONES:
        stops = theme.derive_gloss(tones)
        assert [p for p, _ in stops] == [0, 17, 38, 58, 79, 100] and stops[2][1] == f'#{tones[0]:06x}'
    assert all(h[1:3] == h[3:5] == h[5:7] for _, h in theme.derive_gloss(theme.ACCENT_TONES[0]))  # Graphite stays grey
    assert theme.derive_gloss(theme.ACCENT_TONES[4]) != [(p, f'#{c:06x}') for p, c in theme.GLOSS]  # Cobalt is near, not Classic Blue


@pytest.mark.parametrize('half', ['dark', 'light'])
def test_a_band_stated_in_one_half_is_not_derived(half):
    # a band stated in one half is stated: half() flips it for the other, so derive="gloss" leaves the row alone
    t = resolved(C.replace('"cobalt"/>', '"cobalt graphite" derive="gloss"/>', 1) + f'<colorScheme name="cobalt" {half}.sel.stops="#08a6e7 0, #0065d6 100"/>')
    band = ((0, 0x08a6e7), (100, 0x0065d6))
    dark, light = theme.scheme_half(t['schemes'][4], False), theme.scheme_half(t['schemes'][4], True)
    assert (dark['sel'], light['sel'], dark['sel_top'], light['sel_top']) == (band, band, 0, 0)
    assert t['schemes'][0]['sel'] == tuple((p, int(h[1:], 16)) for p, h in theme.derive_gloss(theme.ACCENT_TONES[0]))  # Graphite, derived


def test_classic_light_second_line_is_its_grey_at_its_size():
    n = theme.builtins()[1]
    # one value in stock's terms, which the light half flips onto #6b6d6b
    assert {s['muted'] for i, s in enumerate(n['schemes']) if n['accents'] >> i & 1} == {0x949294}
    assert n['font_px[ROLE_META]'] == 17 and n['font[ROLE_META]'] == 0  # stock's regular
    assert (n['font[ROLE_INFO]'], n['font_px[ROLE_INFO]']) == (2, 17)  # the theme's face, small
    assert n['np_info_value'] == 0x8a8a8a and n['light']['np_info_value'] == 0x8e9196  # the info rows' values, final
    assert (n['font_px[ROLE_TITLE]'], n['font_px[ROLE_TALL]'], n['font[ROLE_TALL]']) == (27, 28, 2)  # a two-line row's title, Classic's face
    assert n['sep'] == ((0, 0), (50, 0x2c2c2c), (100, 0)) and n['light']['sep'] == ((0, 0xfcfcfc), (50, 0xe2e3e2), (100, 0xf7f7f7))


def test_classic_light_list_titles_and_home_rows_are_27_px():
    n = theme.builtins()[1]
    assert (n['font_px[ROLE_TITLE]'], n['font_px[ROLE_NP]'], n['font_px[ROLE_HOME]'], n['font[ROLE_HOME]']) == (27, 27, 27, 2)
    assert [(t['font_px[ROLE_TITLE]'], t['font_px[ROLE_NP]'], t['font_px[ROLE_HOME]']) for t in set0_builtins()] == [(0, 0, 0), (0, 0, 0)]


def test_modern_home_rows_are_stock_and_its_title_bold():
    modern = set0_builtins()[1]
    assert (modern['font[ROLE_HOME]'], modern['font[ROLE_NP]']) == (0, 1)


def test_classic_blue_own_scheme():
    classic_light = theme.builtins()[1]
    blue = classic_light['schemes'][9]
    assert (blue['label'], blue['base'], blue['sel'], blue['light']['sel_top'], blue['sel_edge']) == ('Classic Blue', 4, theme.GLOSS, theme.GLOSS_TOP, theme.GLOSS_EDGE)
    # the light rim is the light half's; on black the dark half's top row is the band's first stop, no rim
    # (as emitted: half() fills a dark 0 from the light half), and a tall row's second row too; it ends on its dark edge
    for i, s in enumerate(classic_light['schemes']):
        if s['sel']:
            dark, light = theme.scheme_half(s, False), theme.scheme_half(s, True)
            assert dark['sel_top'] == dark['sel_tall_top'] == s['sel'][0][1] and dark['sel_tall_line'] == dark['sel_tall_edge'], i
            assert light['sel_tall_top'] != s['sel'][0][1] and light['sel_tall_line'] != light['sel_tall_edge'], i
    assert classic_light['accents'] == 0x37f and classic_light['opts'][4] == opt('SETTING_ACCENT', default=9, preset=9, values=0x37f)
    # each offered band is the same in both halves (Graphite's grey gloss is designed, not flipped)
    assert all(theme.scheme_half(s, True)['sel'] == theme.scheme_half(s, False)['sel'] for s in classic_light['schemes'])
    assert classic_light['schemes'][4]['sel'] == tuple((p, int(h[1:], 16)) for p, h in theme.derive_gloss(theme.ACCENT_TONES[4]))
    assert classic_light['schemes'][7]['sel'] == ()  # Ivory, not offered


@pytest.mark.parametrize('body, message', [
    ('<colorScheme name="sky" base="teal" label="Sky"/>', 'sky: unknown base teal'),
    ('<colorScheme name="sky" base="cobalt"/>', 'sky: an own scheme needs a label'),
    ('<colorScheme name="sky" base="cobalt" label=""/>', 'sky: an own scheme needs a label'),
    ('<colorScheme name="cobalt" base="cobalt" label="C"/>', 'cobalt: an ACCENTS row takes no base'),
    ('<colorSchemes accents="graphite" derive="matte"/>', 'colorSchemes.derive: unknown value matte'),
    *((f'<colorScheme name="sky" base="cobalt" label="{label}"/>', 'sky: label: expected 1..48 bytes, no control characters')
      for label in ('A\x7fB', 'A\x85B', 'x' * 49, 'é' * 25)),  # as a theme's name
])
def test_own_scheme_rejects(body, message):
    assert error(resolved, body) == f't.xml:1: {message}'


def test_own_scheme_label_takes_48_bytes():
    t = resolved(f'<colorScheme name="sky" base="cobalt" label="{"é" * 24}"/>')
    assert theme.scheme_names(t)[9:] == ['sky'] and t['schemes'][9]['label'] == 'é' * 24


def test_own_scheme_restated_is_its_row():
    # a theme on Classic restating Classic Blue, its own scheme, changes that row and adds none
    t = resolved('<colorScheme name="classic-blue" base="cobalt" label="Classic Blue" muted="#123456"/>',
                 'id="t" name="T" index="2" extends="classic-light"')
    assert theme.scheme_names(t)[9:] == ['classic-blue'] and t['schemes'][9]['muted'] == 0x123456
    assert t['accents'] == 0x37f  # Classic's: every row but Ivory's


def test_emit_own_scheme(tmp_path):
    inc = boot_inc(tmp_path, theme.builtins())
    assert '    { .tones = accents[4], ' in inc.split('static const scheme_t classic_light_schemes[] = {')[1].split('\n')[10]
    assert '.label = "Classic Blue" }' in inc and inc.count('.n_schemes = 9,') == 2 and '.n_schemes = 10,' in inc
    assert inc.count('/ sizeof *minimal_schemes >= ACCENT_N') == 1


def test_own_option():
    m, n, c = theme.builtins()
    row_art = own('ROWART', 'Row Art', ('Off', 'On'), ('classic-light-text', 'classic-light'), default=1, preset=1)
    assert n['opts'][-1] == row_art and [o['key'] for o in m['opts'] + c['opts'] if o.get('key')] == ['STATUSBAR', 'SHOWQ2POD', 'STATUSBAR', 'SHOWQ2POD']
    assert theme.geometry(n) == CLASSIC_LIGHT and theme.geometry(n, {'ROWART': 'Off'}) == CLASSIC_LIGHT_TEXT
    assert theme.geometry(n, {'ROWART': 'On'}) == CLASSIC_LIGHT and theme.geometry(m, {'ROWART': 'Off'}) is SET0
    assert theme.theme_keys([m, n]) == {'STATUSBAR': (4, 0, STATUSBAR), 'SHOWQ2POD': (2, 1, SHOWQ2POD), 'CLASSIC_LIGHT_ACCENT': (10, 9, None), 'HOMELIB': (2, 1, HOMELIB), 'ROWPREVIEW': (2, 1, ROWPREVIEW), 'ROWART': (2, 1, row_art)}
    assert {'ACCENT', 'MINIMAL_ACCENT', 'HOME', 'BATTERY', 'THEME', 'LIGHT', 'VIS'} <= theme.PAYLOAD_KEYS
    assert not {'ROWART', 'CLASSIC_LIGHT_ACCENT'} & theme.PAYLOAD_KEYS and set(theme.ACCENT_KEYS) == {'ACCENT', 'MINIMAL_ACCENT'}
    assert not hasattr(theme, 'ROW_ART_OFF')


V = '<value label="Off" set="ipod"/><value label="On" set="ipod"/>'
@pytest.mark.parametrize('body, message', [
    ('<option key="ART" label="Art"><value label="Off" set="ipod"/></option>', 'ART: 2 to 10 values expected'),
    ('<option key="ART" label="Art"><value label="Off" set="ipod"/><value label="Off" set="ipod"/></option>', 'ART: duplicate value Off'),
    ('<option key="ART" label="Art"><value label="Off" set="flat"/><value label="On" set="ipod"/></option>', 'unknown geometry set "flat"'),
    ('<option key="ART" label="Art"><value label="Off"/><value label="On" set="ipod"/></option>', 'ART: every value names a set, or none does'),
    ('<option key="ART" label="Art"><value label="Off"/><value label="On"/></option>', 'ART: a plain choice the payload does not read (PLAIN_KEYS)'),
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


def test_payload_option_values():
    # values: the bitmask of the values the row takes; default: what a stored value outside them reads as
    t = resolved('<option setting="battery" values="icon inside" default="inside" preset="icon"/><option setting="accent" key="ACCENT"/>')
    assert t['opts'][0] == opt('SETTING_BATTERY', default=3, preset=0, values=0b1001)
    assert all(o['values'] == 0 for b in set0_builtins() for o in b['opts'] if o['setting'] != 'SETTING_ACCENT')


@pytest.mark.parametrize('body, message', [
    ('<option setting="battery" values="icon inside"/>', 'battery: values need a default, what a stored value outside them reads as'),
    ('<option setting="battery" values="icon" default="icon"/>', 'battery: 2 or more values expected'),
    ('<option setting="battery" values="icon icon" default="icon"/>', 'battery: 2 or more values expected'),
    ('<option setting="battery" values="icon dim" default="icon"/>', 'battery: unknown value dim'),
    ('<option setting="battery" values="icon inside" default="both"/>', 'battery: default both is not in values'),
    ('<option setting="battery" values="icon inside" default="icon" preset="both"/>', 'battery: preset both is not in values'),
    ('<option setting="light" values="off on" default="on" keep="auto"/>', 'light: keep auto is not in values'),
    ('<option setting="accent" key="ACCENT" values="cobalt green" default="cobalt"/>', 'accent: colorSchemes states its values'),
    (f'<option key="ART" label="Art" values="Off On">{V}</option>', 'ART: its <value>s are its values'),
])
def test_payload_option_values_rejects(body, message):
    assert error(resolved, body) == f't.xml:1: {message}'


def test_builtins_rejects_a_shared_own_key(tmp_path):
    """Themes sharing an own key share its stored value, so they must declare the same values and default;
    each presets it as it likes (as Minimal and Classic preset LIGHT). Both on a set of their own: an option Minimal
    lacks can't share its set."""
    set0_themes(tmp_path)
    def put(folder, index, label, presets=''):
        (tmp_path/folder).mkdir(exist_ok=True)
        (tmp_path/folder/'theme.xml').write_text(
            f'<theme format="1" id="{folder}" name="{folder}" index="{index}" extends="modern"><geometry name="pico"/><geometry set="pico"/>'
            f'<option setting="accent" key="ACCENT" default="graphite"/><option key="ART" label="{label}"{presets}>{V.replace("ipod", "pico")}</option></theme>')
    put('a', 2, 'Art', ' preset="On"'); put('b', 3, 'Art', ' preset="Off" keep="On"')
    assert list(theme.theme_keys(theme.builtins(tmp_path))) == ['STATUSBAR', 'SHOWQ2POD', 'ART']  # one key, one slot each
    assert {t['id']: (t['opts'][-1]['preset'], t['opts'][-1]['keep']) for t in theme.builtins(tmp_path)[2:]} == {'a': (1, -1), 'b': (0, 1)}
    put('b', 3, 'Row Art')
    assert error(theme.builtins, tmp_path) == "b/theme.xml:1: ART is a's, with other values or default"


def test_emit_own_options(tmp_path):
    inc = boot_inc(tmp_path, theme.builtins())
    classic_light_opts = inc.split('static const option_t classic_light_opts[] = {\n')[1].split('};')[0].splitlines()
    assert classic_light_opts[-1] == '    { .setting = SETTING_OWN + 5, .def = 1, .preset = 1, .keep = -1, .values = 0x0 },'
    assert inc.count('.accent_slot = &st.own[2],') == 1 and 'classic_light_accent' not in inc
    assert ('const theme_key_t theme_keys[THEME_KEYS] = {\n'
            '    { .key = "STATUSBAR", .label = "Status Bar", .names = (const char *const[]){"Theme", "Minimal", "Modern", "Classic"}, .sets = 0, .n = 4, .def = 0 },\n'
            '    { .key = "SHOWQ2POD", .label = "Show Wordmark", .names = (const char *const[]){"Off", "On"}, .sets = 0, .n = 2, .def = 1 },\n'
            '    { .key = "CLASSIC_LIGHT_ACCENT", .label = 0, .names = 0, .sets = 0, .n = 10, .def = 9 },\n'
            '    { .key = "HOMELIB", .label = "Library on Home", .names = (const char *const[]){"Off", "On"}, .sets = 0, .n = 2, .def = 1 },\n'
            '    { .key = "ROWPREVIEW", .label = "Row Preview", .names = (const char *const[]){"Off", "On"}, .sets = 0, .n = 2, .def = 1 },\n'
            '    { .key = "ROWART", .label = "Row Art", .names = (const char *const[]){"Off", "On"}, '
            '.sets = (const unsigned char[]){GEOM_CLASSIC_LIGHT_TEXT, GEOM_CLASSIC_LIGHT}, .n = 2, .def = 1 },\n};') in inc
    assert inc.index('const theme_key_t theme_keys') > inc.index('enum { GEOM_IPOD, GEOM_CLASSIC_LIGHT, GEOM_CLASSIC_LIGHT_TEXT, GEOM_N };')
    assert '#define OWN_STATUSBAR 0\n#define OWN_SHOWQ2POD 1\n#define OWN_CLASSIC_LIGHT_ACCENT 2\n#define OWN_HOMELIB 3\n#define OWN_ROWPREVIEW 4\n#define OWN_ROWART 5\n' in inc


def test_classic_light_sets_from_its_theme_file():
    """Classic's sets are its theme.xml's: classic_light states every Geometry field (SET0's defaults never reach it),
    classic-light-text extends it. No tool names them."""
    m, n, c = theme.builtins()
    assert theme.sets(n) == {'ipod': SET0, 'classic-light': CLASSIC_LIGHT, 'classic-light-text': CLASSIC_LIGHT_TEXT} and theme.geometry(n) == CLASSIC_LIGHT
    assert theme.sets(m) == theme.sets(c) == {'ipod': SET0} and (m['geoms'], c['geoms']) == ({}, {})
    decl = {e.attrs['name']: e.attrs for e in theme.load(THEMES/'classic-light'/'theme.xml').children
            if e.tag == 'geometry' and 'name' in e.attrs}
    assert set(decl['classic-light']) == {f.name for f in dataclasses.fields(Geometry)} and decl['classic-light-text']['extends'] == 'classic-light'
    assert not hasattr(ipod, 'CLASSIC_LIGHT') and not hasattr(ipod, 'CLASSIC_LIGHT_TEXT')


def test_geometry_declared():
    # An omitted field is SET0's, or its extends= set's; <geometry set> names a declared set; an extending
    # theme inherits its base's sets.
    t = resolved('<geometry name="pico" bar="26"/><geometry name="pico-2" extends="pico" text_x="20"/>'
                 '<geometry set="pico-2"/>')
    pico = dataclasses.replace(SET0, name='pico', bar=26)
    assert t['geoms'] == {'pico': pico, 'pico-2': dataclasses.replace(pico, name='pico-2', text_x=20)}
    assert theme.geometry(t) == t['geoms']['pico-2'] and theme.geometry_sets([t]) == [SET0, t['geoms']['pico-2']]
    u = resolved('', 'id="u" name="U" index="3" extends="classic-light"')
    assert theme.sets(u) == theme.sets(theme.builtins()[1]) and theme.geometry(u) == CLASSIC_LIGHT


def test_geometry_ranges():
    """Every Geometry field has a range, so a task that adds a field adds one; SET0 and Classic's sets lie inside."""
    assert set(theme.GEOM_RANGES) == {f.name for f in dataclasses.fields(Geometry)} - {'name'}
    assert [theme.check_set(g) for g in (SET0, CLASSIC_LIGHT, CLASSIC_LIGHT_TEXT)] == [None, None, None]
    assert theme.check_set(dataclasses.replace(SET0, name='x', set_rows=5)) == 'geometry.set_rows: 5 outside 1..4'


@pytest.mark.parametrize('body, message', [
    ('<geometry name="Pico"/>', 'geometry.name: expected [a-z][a-z0-9-]*, 1..24 bytes, not n'),
    ('<geometry name="pico-text-and-much-much-more"/>', 'geometry.name: expected [a-z][a-z0-9-]*, 1..24 bytes, not n'),
    ('<geometry name="n"/>', 'geometry.name: expected [a-z][a-z0-9-]*, 1..24 bytes, not n'),
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
    set0_themes(tmp_path)
    def put(folder, index, bar):
        (tmp_path/folder).mkdir(exist_ok=True)
        (tmp_path/folder/'theme.xml').write_text(
            f'<theme format="1" id="{folder}" name="{folder}" index="{index}" extends="modern">'
            f'<geometry name="pico" bar="{bar}"/><geometry set="pico"/>'
            '<option setting="accent" key="ACCENT" default="graphite"/></theme>')
    put('a', 2, 26); put('b', 3, 26)
    assert [theme.geometry(t).name for t in theme.builtins(tmp_path)[2:]] == ['pico', 'pico']
    put('b', 3, 28)
    assert error(theme.builtins, tmp_path) == "b/theme.xml:1: geometry set pico is a's, with other values"




def boot_inc(tmp_path, themes, **kw):
    theme.emit(themes, tmp_path, **kw)
    return (tmp_path/'themes.h').read_text() + (tmp_path/'themes.inc').read_text()


def test_emit_boot_tables(tmp_path):
    inc = boot_inc(tmp_path, set0_builtins())
    names = ipod.VALUE_NAMES
    sites = [(int(a, 16), g['value']) for g in ipod.AUDIT['immediates'] if isinstance(g['value'], str) for a, _ in g['sites']]
    assert len(sites) == 89
    assert 'enum { GEOM_IPOD, GEOM_N };' in (tmp_path/'themes.h').read_text() and 'GEOM_N };' not in (tmp_path/'themes.inc').read_text()
    assert 'enum { ' + ', '.join(f'G_{n.upper()}' for n in names) + ', G_N };' in inc
    assert '#define BOOT_SITES 89' in inc and '#define CLIENT_H_MAX 290' in inc
    assert 'const boot_site_t boot_sites[BOOT_SITES] = {' + ', '.join(f'{{{a:#x}, G_{v.upper()}}}' for a, v in sites) + '};' in inc
    row = ', '.join(str(ipod.immediate_values(SET0)[n]) for n in names)
    assert f'const volatile unsigned short boot_values[GEOM_N][G_N] = {{{{{row}}}}};' in inc
    assert 'const char *const geom_names[GEOM_N] = {"ipod"};' in inc
    assert 'const volatile ui_redirect_t ui_redirects[] = {{0, 0}};' in inc
    assert re.findall(r'\.geom = (\d+)', inc) == ['0', '0']
    assert all(s in inc for s in geom_vals_text([SET0])) and '#define THEME_GEOM_OFF 4' in inc


def geom_vals_text(sets):
    return ('enum { ' + ', '.join(f'V_{n.upper()}' for n in ipod.GEOM_VALS) + ', V_N };',
            'geom_vals[GEOM_N][V_N] = {' + ', '.join('{' + ', '.join(str(getattr(g, n)) for n in ipod.GEOM_VALS) + '}' for g in sets) + '};')


def test_emit_geom_vals(tmp_path):
    inc = boot_inc(tmp_path, theme.builtins())
    assert all(s in inc for s in geom_vals_text([SET0, CLASSIC_LIGHT, CLASSIC_LIGHT_TEXT]))
    assert ipod.GEOM_VALS[:3] == ('bar', 'set_row', 'set_icon')
    assert [[getattr(g, n) for n in ipod.GEOM_VALS[:3]] for g in (SET0, CLASSIC_LIGHT)] == [[30, 68, 40], [26, 32, 0]]


def test_emit_refuses_a_long_redirect(tmp_path):
    # 'classic-light-text_' + 44 characters is 63 bytes plus the NUL: it fits; 45 does not.
    boot_inc(tmp_path, theme.builtins(), redirects={'x' * 44: 2})
    with pytest.raises(ValueError, match='exceeds ringnav_ui_load'):
        boot_inc(tmp_path, theme.builtins(), redirects={'x' * 45: 2})


def test_emit_second_set(tmp_path, test_set):
    m, c = set0_builtins()
    inc = boot_inc(tmp_path, [m, dict(c, geom_set='test')], redirects={'system_bar': 2, 'dialog/volume_dialog': 2})
    assert 'enum { GEOM_IPOD, GEOM_TEST, GEOM_N };' in inc and '{"ipod", "test"}' in inc
    rows = ['{' + ', '.join(str(ipod.immediate_values(g)[n]) for n in ipod.VALUE_NAMES) + '}' for g in (SET0, TEST)]
    assert '= {' + ', '.join(rows) + '};' in inc
    assert 'ui_redirects[] = {{"dialog/volume_dialog", 2}, {"system_bar", 2}, {0, 0}};' in inc
    assert re.findall(r'\.geom = (\d+)', inc) == ['0', '1']


def test_emit_refuses_a_set0_redirect(tmp_path):
    with pytest.raises(ValueError, match='system_bar: SET0 never redirects'):
        boot_inc(tmp_path, theme.builtins(), redirects={'system_bar': 1})


def test_classic_light_builtin():
    m, n, c = theme.builtins()
    assert (n['id'], n['name'], n['index'], n['geom_set'], n['accent_key'], n['accents']) == ('classic-light', 'Classic', 1, 'classic-light', 'CLASSIC_LIGHT_ACCENT', 0x37f)
    assert n['opts'] == [STATUSBAR, opt('SETTING_TIMETITLE', preset=1), SHOWQ2POD, opt('SETTING_LIGHT', preset=1, keep=2),
                         opt('SETTING_ACCENT', default=9, preset=9, values=0x37f), opt('SETTING_HOME', preset=0), opt('SETTING_SHORTCUT'),
                         opt('SETTING_BATTERY', default=3, values=0b1011), HOMELIB, ROWPREVIEW, own('ROWART', 'Row Art', ('Off', 'On'), ('classic-light-text', 'classic-light'), default=1, preset=1)]
    assert theme.geometry_sets([m, c, n]) == [SET0, CLASSIC_LIGHT, CLASSIC_LIGHT_TEXT]


def test_emit_classic_light_sets(tmp_path):
    inc = boot_inc(tmp_path, theme.builtins())
    assert 'enum { GEOM_IPOD, GEOM_CLASSIC_LIGHT, GEOM_CLASSIC_LIGHT_TEXT, GEOM_N };' in inc and '#define CLIENT_H_MAX 294' in inc
    assert 'const char *const geom_names[GEOM_N] = {"ipod", "classic-light", "classic-light-text"};' in inc
    assert re.findall(r'\.geom = (\d+)', inc) == ['0', '1', '0'] and '.accent_slot = &st.own[2],' in inc


def test_rows_key():
    t = theme.builtins()[1]
    assert t['painter[PAINT_VOLUME]'] == 1
    assert (t['letter_raw'], t['letter_px']) == (1, 60)  # the fast-scroll letter: final colours
    assert t['painter[PAINT_CONFIRM]'] == 1 and t['painter[PAINT_PAGES]'] == 1 and t['art_final'] == t['icons_badge_ink'] == 1
    assert t['painter[PAINT_SCRUB]'] == 1 and all(len(t[f'np_rows[{i}]']) == 31 for i in range(4))
    assert t['np_rows[2]'][0] == 0xffe7e7e7 and t['np_rows[0]'][17] == 0x56424142  # #rrggbb is opaque, #rrggbbaa its own alpha
    with pytest.raises(theme.ThemeError, match='up to 31'):
        resolved('<view name="playing"><element name="scrubber" light_track="#12345"/></view>')


def test_easings_are_awtk():
    assert len(theme.EASINGS) == 25 and theme.EASINGS[O['SLIDE_EASING']] == 'sin_inout' \
        and theme.EASINGS[O['EASE_CUBIC_OUT']] == 'cubic_out' and theme.EASINGS[2] == 'quadratic_out' \
        and theme.EASINGS[19] == 'back_out' and theme.EASINGS[23] == 'bounce_inout' \
        and theme.EASINGS[O['EASE_FOLLOW']] == 'follow'  # ours, one past AWTK's 24


def test_motion_today(tmp_path):  # Minimal: the asset hint kept (0), today's numbers
    inc = boot_inc(tmp_path, theme.builtins()[:1])
    assert inc.count('.motion[MOTION_PAGE].hint = 0,') == 1 and ipod.SLIDE == 'htranslate(duration=120)'
    assert [t['motion.page.ms'] for t in theme.builtins()[:1]] == [120]  # the key as MINIMAL spells it
    for f, v in (('motion[MOTION_SWAP].ms', 120), ('motion[MOTION_SWAP].easing', 8), ('motion[MOTION_STEP].ms', 200),
                 ('motion[MOTION_STEP_FAST].ms', 120), ('motion[MOTION_STEP].easing', 8), ('recall_ms', 300), ('bump_px', 6)):
        assert inc.count(f'.{f} = {v},') == 1, f
    assert inc.count('.motion[MOTION_COVERFLOW].hint = 0,') == 1 and inc.count('.motion[MOTION_INPLACE].ms = 0,') == 1
    assert '.anim' not in inc
    # theme.h's order is the emitted kinds', which test/patch.py indexes by
    motions = re.search(r'enum \{\s*MOTION_PAGE.*?\}', (theme.ROOT/'patch/theme.h').read_text(), re.S)
    assert motions
    assert re.findall(r'MOTION_\w+', motions[0]) \
        == [f'MOTION_{k.upper()}' for k in (*theme.MOTION_WINDOW, *theme.MOTION_WIDGET)] + ['MOTION_N']


MOTION_CLASSIC_LIGHT = {**MOTION_TODAY,
                        'motion.swap.ms': 120, 'motion.swap.easing': 0, 'motion.inplace.ms': 120, 'motion.inplace.easing': 5, 'recall_ms': 0, 'bump_px': 0,
                        'motion.coverflow_step.ms': 81, 'motion.coverflow_step.easing': 24, 'motion.coverflow_step_fast.ms': 81, 'motion.coverflow_step_fast.easing': 24}


@pytest.mark.parametrize('kind', [k for k in (*theme.MOTION_WINDOW, *theme.MOTION_WIDGET) if not k.startswith('coverflow_step')])
def test_follow_is_cover_flow_steps_alone(kind):  # stock's easing_get has no such id, and a window's anim_hint no such name
    with pytest.raises(theme.ThemeError, match=rf'motion\.{kind}\.easing: follow is only for coverflow_step and coverflow_step_fast'):
        theme.validate({**theme.builtins()[1], f'motion.{kind}.easing': theme.EASINGS.index('follow')}, 't.xml')


def test_motion_classic_light(tmp_path):
    light = theme.builtins()[1]
    assert {k.field: light[k.field] for k in theme.SCHEMA.values() if k.cls == 'motion'} == MOTION_CLASSIC_LIGHT
    inc = boot_inc(tmp_path, theme.builtins())
    assert inc.count('.motion[MOTION_PAGE].hint = 0,') == 3  # every theme's pages: the asset's own
    assert '.hint = "' not in inc  # no window animates beyond stock's own slide: no push, no dialog fade


def test_motion_composes_hints(tmp_path):
    t = resolved('<view name="motion"><element name="page" ms="300" easing="sin_inout"/>'
                 '<element name="coverflow" anim="slide_left" ms="230" easing="cubic_out"/>'  # the animator's default: no easing=
                 '<element name="playing" anim="fade" easing="back_out" ms="0"/>'  # 0: the window as it is
                 '<element name="step" ms="250" easing="quadratic_out"/><element name="inplace" ms="300"/>'
                 '<element name="recall" ms="0"/><element name="bump" px="0"/></view>')
    assert t['motion.playing.anim'] == 2 and t['motion.step.easing'] == 2
    inc = boot_inc(tmp_path, [*theme.builtins()[:1], t])
    for line in ('.motion[MOTION_PAGE].hint = "htranslate(duration=300,easing=sin_inout)",',
                 '.motion[MOTION_COVERFLOW].hint = "slide_left(duration=230)",',
                 '.motion[MOTION_STEP].ms = 250,', '.motion[MOTION_STEP].easing = 2,',
                 '.motion[MOTION_INPLACE].ms = 300,', '.recall_ms = 0,', '.bump_px = 0,'):
        assert inc.count(line) == 1, line
    assert inc.count('.motion[MOTION_PLAYING].hint = 0,') == 2


@pytest.mark.parametrize('element, message', [
    ('<element name="page" easing="springy"/>', r'motion\.page\.easing: springy'),
    ('<element name="page" ms="2001"/>', r'motion\.page\.ms: 2001 outside'),
    ('<element name="coverflow" anim="spin"/>', r'motion\.coverflow\.anim: spin'),
    ('<element name="step" anim="fade"/>', r'motion\.step\.anim'),  # widget kinds take no animator
])
def test_motion_rejects(element, message):
    with pytest.raises(theme.ThemeError, match=message):
        resolved(f'<view name="motion">{element}</view>')


def test_bar_font_size_is_the_sets_bar_px(tmp_path):
    """The bar's label is laid out at its set's bar_px (tools/ipod.py status_metrics): its text size must match."""
    xml = (theme.ROOT/'assets/themes/classic-light/theme.xml').read_text().replace('font="classic-light" font_size="21"', 'font="classic-light" font_size="24"')
    for d, src in (('minimal', (theme.ROOT/'assets/themes/minimal/theme.xml').read_text()), ('classic-light', xml)):
        (tmp_path/d).mkdir(); (tmp_path/d/'theme.xml').write_text(src)
    with pytest.raises(theme.ThemeError, match=r"bar.title.font_size 24 is not set classic-light's bar_px 21$"):
        theme.builtins(tmp_path)


@pytest.mark.parametrize('ends, message', [('top="58" bottom="261"', 'list.scrollbar: top 58 is under the glass (inset 4 > right 2 + 1)'),
                                           ('top="59" bottom="263"', 'list.scrollbar: bottom 263 is under the glass (inset 4 > right 2 + 1)')])
def test_scrollbar_clears_the_glass(ends, message):
    t = resolved(f'<view name="list"><element name="scrollbar" w="4" right="2" {ends}/></view>')
    assert error(theme.validate, t, 't.xml') == f't.xml:1: {message}'


@pytest.mark.parametrize('attrs, message', [('top="100" bottom="100" min="16" thumb="#8f98a8"', 'list.scrollbar: top 100 is not above bottom 100'),
                                            ('top="200" bottom="100" min="16" thumb="#8f98a8"', 'list.scrollbar: top 200 is not above bottom 100'),
                                            ('top="59" bottom="261" thumb="#8f98a8"', 'list.scrollbar: no min'),
                                            ('top="59" bottom="261" min="16"', 'list.scrollbar: no thumb colour')])
def test_scrollbar_rejects(attrs, message):
    t = resolved(f'<view name="list"><element name="scrollbar" w="4" right="2" {attrs}/></view>')
    assert error(theme.validate, t, 't.xml') == f't.xml:1: {message}'


def test_scrollbar_at_the_glass_or_absent_passes():
    theme.validate(resolved('<view name="list"><element name="scrollbar" w="4" right="2" top="59" bottom="261" min="16" thumb="#8f98a8"/></view>'), 't.xml')
    theme.validate(resolved('<view name="list"><element name="scrollbar" w="4" right="2" top="59" bottom="261" min="16" light.thumb="#8f98a8"/></view>'), 't.xml')
    theme.validate(resolved('<view name="list"><element name="scrollbar" right="2" top="0" bottom="320"/></view>'), 't.xml')
