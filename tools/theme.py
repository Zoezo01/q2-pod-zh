"""Built-in themes: assets/themes/<id>/theme.xml in the Q2 theme XML, read, checked and written out as
the payload's theme table (themes.inc)."""
import colorsys
import dataclasses
import itertools
import json
import re
from build import ROOT, check
from ipod import AUDIT, GEOM_VALS, INC, O, SET0, VALUE_NAMES, Geometry, immediate_values


ACCENTS_BLOCK = re.search(r'#define ACCENTS (?:.*\\\n)*.*', INC)[0]
ACCENT_NAMES = [n.lower() for n in re.findall(r'\}\s*,?\s*/\* (\w+)', ACCENTS_BLOCK)]
ACCENT_TONES = [[int(x, 16) for x in re.findall(r'0x([0-9a-fA-F]{6})', row)] for row in re.findall(r'\{([^}]*)\}', ACCENTS_BLOCK)]
ALL_ACCENTS = (1 << len(ACCENT_NAMES)) - 1
SETTINGS = {'light': ('SETTING_LIGHT', ('off', 'on', 'auto')), 'accent': ('SETTING_ACCENT', ACCENT_NAMES),
            'corners': ('SETTING_ROUNDED', ('square', 'round')), 'home': ('SETTING_HOME', ('split', 'full', 'carousel')),
            'battery': ('SETTING_BATTERY', ('icon', 'percent', 'both', 'inside')),
            'shortcut': ('SETTING_SHORTCUT', ('rockbox', 'artists', 'albums', 'favorites', 'rated_albums', 'shuffle', 'games', 'playlists', 'podcasts', 'audiobooks')),
            'time_title': ('SETTING_TIMETITLE', ('off', 'on')),
            'np_layout': ('SETTING_NPCENTRED', ('ipod', 'centred'))}
ACCENT_KEYS = {'ACCENT': '&st.accent', 'MINIMAL_ACCENT': '&st.minimal_accent'}
# The IPOD keys the payload reads itself (navigation.c setting_keys[], every "IPOD", "<KEY>" literal): a
# theme's own key is never one of them.
PAYLOAD_KEYS = frozenset(
    re.findall(r'"([A-Z0-9_]+)"', re.search(r'setting_keys\[\] = \{([^}]*)\}', (ROOT/'patch/navigation.c').read_text())[1])
    + [k for f in sorted((ROOT/'patch').glob('*.c')) for k in re.findall(r'"IPOD", "([A-Z0-9_]+)"', f.read_text())])
# value name -> its C name (theme.h); the font keys take FONTS
FONTS = {'regular': 'FONT_DEFAULT', 'bold': 'FONT_BOLD', 'classic-light': 'FONT_CLASSIC_LIGHT'}
CHOICES = {'list.selection.painter': {'card': 'SEL_CARD', 'stops': 'SEL_STOPS', 'bar': 'SEL_BAR'},
           'bar.fill.painter': {'flat': 'BAR_FLAT', 'gloss': 'BAR_GLOSS'},
           'bar.battery.painter': {'stock': 'BATT_STOCK', 'classic-light': 'BATT_CLASSIC_LIGHT'},
           **dict.fromkeys(('playing.title.font', 'list.row.font', 'list.row.meta_font', 'list.row.tall_font', 'bar.title.font'), FONTS),
           'playing.layout': {'ipod': 'NP_IPOD', 'centred': 'NP_CENTRED'},
           'scheme.mode': {'derive': 'SCHEME_DERIVE', 'light': 'SCHEME_LIGHT', 'dark': 'SCHEME_DARK'}}


@dataclasses.dataclass(frozen=True)
class Key:
    path: str
    field: str
    type: str
    lo: int
    hi: int
    default: object
    cls: str  # 'live' | 'open' | 'boot'
    final: bool = False  # drawn as stated: each half its own value (light.<key>, dark.<key>)


def key(path, field, type, hi=None, final=False):
    hi = {'bool': 1, 'color': 0xffffff, 'enum': len(CHOICES.get(path, ())) - 1}.get(type, hi)
    return Key(path, field, type, 0, hi, 0, 'live', final)


# theme_t declaration order; the scheme.* keys are scheme_t's.
SCHEMA = {k.path: k for k in (
    key('list.selection.painter', 'painter[PAINT_SELECTION]', 'enum'),
    key('bar.fill.painter', 'painter[PAINT_BAR]', 'enum'),
    key('bar.battery.painter', 'painter[PAINT_BATTERY]', 'enum'),
    key('home.chevron.visible', 'chevron_home', 'bool'),
    key('list.chevron.selected', 'chevron_sel', 'bool'),
    key('list.art.x', 'art_x', 'int', 375),  # a row with art, on screen: the cover, 0: stock's layout
    key('list.art.text_x', 'art_text_x', 'int', 375),  # ... its text's first and last columns
    key('list.art.text_end', 'art_text_end', 'int', 375),
    key('list.art.placeholder', 'art_placeholder', 'int', 51),  # stock's grey tiles (list_folder, list_song) this size, centred in their row; 0: stock's
    key('list.row.text_end', 'row_text_end', 'int', 375),  # a two-line row without art (Songs'): its text's last column on screen
    Key('list.separator.stops', 'sep', 'stops', 0, 0, (), 'live', True),  # ... its last rows, one stop a row, final
    key('list.row.tall_font', 'font[ROLE_TALL]', 'enum'),  # a two-line row's title
    key('list.row.tall_px', 'font_px[ROLE_TALL]', 'int', 64),
    key('list.chevron.x', 'chevron_x', 'int', 375),  # the theme's own chevron's box on screen in a full-width list; 0: stock's
    key('home.chevron.x', 'home_chevron_x', 'int', 375),  # ... and in Split's list
    key('bar.title.caps', 'caps_mark', 'bool'),
    key('home.rows.caps', 'caps_home', 'bool'),
    key('playing.album.caps', 'caps_np', 'bool'),
    key('list.icons.mono', 'icons_mono', 'bool'),
    key('list.icons.badge_muted', 'icons_badge_muted', 'bool'),
    key('list.icons.addon_mono', 'icons_addon_mono', 'bool'),
    key('playing.title.font', 'font[ROLE_NP]', 'enum'),
    key('playing.album.opacity', 'np_album_op', 'int', 255),
    key('playing.state.opacity', 'np_state_op', 'int', 255),
    key('home.rows.top', 'home_top', 'int', 375),
    key('home.rows.full_row', 'home_full_row', 'int', 375),
    key('home.rows.text_x', 'home_text_x', 'int', 375),
    key('home.rows.label_end_split', 'home_label_end[0]', 'int', 375),
    key('home.rows.label_end_full', 'home_label_end[1]', 'int', 375),
    key('home.rows.font_px', 'font_px[ROLE_NP]', 'int', 64),  # the role's: Now Playing's title follows it
    Key('home.pane.stops', 'home_pane', 'stops', 0, 0, (), 'live', True),  # Split with no cover, final; (): the placeholder image
    key('home.pane.shadow', 'pane_shadow', 'color'),
    key('home.pane.shadow_w', 'pane_shadow_w', 'int', 32),
    Key('home.pane.shadow_a', 'pane_shadow_a', 'alphas', 0, 0, (), 'live'),  # its alpha per column
    key('home.pane.light_a', 'pane_light_a', 'int', 255),  # white, this alpha at the left edge and 0 at the right
    key('home.pane.icon_x', 'pane_icon_x', 'int', 375),  # a row's icon's top-left in the panel; 0: the pane doesn't follow the row
    key('home.pane.icon_y', 'pane_icon_y', 'int', 320),
    key('home.pane.note_x', 'pane_note_x', 'int', 375),
    key('home.pane.note_y', 'pane_note_y', 'int', 320),
    key('home.pane.count_y', 'pane_count_y', 'int', 320),
    key('home.pane.count_w', 'pane_count_w', 'int', 375),
    key('home.pane.count_px', 'pane_count_px', 'int', 64),
    key('settings.rows.top', 'set_top', 'int', 375),
    key('settings.values.text', 'set_text', 'bool'),
    key('playing.scrubber.height', 'np_bar', 'int', 375),
    key('playing.scrubber.scrub_height', 'np_scrub', 'int', 375),
    key('playing.art.square', 'art_square', 'bool'),
    key('bar.battery.fill', 'batt_fill', 'color'),
    key('list.row.font', 'font[ROLE_TITLE]', 'enum'),
    key('list.row.meta_font', 'font[ROLE_META]', 'enum'),
    key('list.row.title_px', 'font_px[ROLE_TITLE]', 'int', 64),
    key('list.row.meta_px', 'font_px[ROLE_META]', 'int', 64),
    key('bar.title.font', 'font[ROLE_BAR]', 'enum'),
    key('bar.title.font_size', 'font_px[ROLE_BAR]', 'int', 64),
    Key('bar.battery.stops', 'batt', 'stops', 0, 0, (), 'live'),
    key('bar.battery.line', 'batt_line', 'color', final=True),  # a grey that must follow the paper
    Key('bar.battery.empty', 'batt_empty', 'stops', 0, 0, (), 'live'),  # the section past the level, final
    key('bar.battery.low', 'batt_low', 'color'),  # the level when low, batt_low_w columns
    key('bar.battery.low_w', 'batt_low_w', 'int', 64),
    key('bar.battery.edge', 'batt_edge', 'color'),  # its bottom row inside the outline
    key('bar.battery.corner_a', 'batt_corner_a', 'int', 255),  # the outline's top corners, the nub's outer ones
    key('bar.battery.foot_a', 'batt_foot_a', 'int', 255),  # its bottom corners
    key('bar.battery.shade_a', 'batt_shade_a', 'int', 255),  # black over the level's last column
    key('bar.battery.soft_a', 'batt_soft_a', 'int', 255),  # the light half's soft outer edge: left of the body, black
    key('bar.battery.soft_top_a', 'batt_soft_top_a', 'int', 255),  # ... and above it
    key('bar.battery.emboss_a', 'batt_emboss_a', 'int', 255),  # the light half's white row under the body
    key('bar.battery.emboss_nub_a', 'batt_emboss_nub_a', 'int', 255),  # ... and under the nub
    Key('bar.icons.bt_dy', 'bar_bt_dy', 'int', -4, 4, 0, 'live'),  # rows Bluetooth and Wi-Fi sit off the row layout's
    Key('bar.icons.wifi_dy', 'bar_wifi_dy', 'int', -4, 4, 0, 'live'),
    key('scheme.card', 'card', 'bool'),
    key('scheme.raw_red', 'raw_red', 'bool'),
    key('scheme.fill', 'fill', 'color'),
    key('scheme.chevron', 'chevron', 'color'),
    key('scheme.muted', 'muted', 'color'),
    key('scheme.bar_light', 'bar_light', 'color'),
    Key('scheme.sel.stops', 'sel', 'stops', 0, 0, (), 'live', True),
    key('scheme.sel.top', 'sel_top', 'color', final=True),
    key('scheme.sel.edge', 'sel_edge', 'color', final=True),
    key('scheme.sel.tall_edge', 'sel_tall_edge', 'color', final=True),  # a row taller than the set's pitch ends on these two rows, 0: its edge
    key('scheme.sel.tall_line', 'sel_tall_line', 'color', final=True),
    key('scheme.sel.tall_top', 'sel_tall_top', 'color', final=True),  # ... and has this second row, 0 in both halves: the stops (a dark 0 takes the light half's: half())
    key('scheme.sel.text', 'sel_text', 'color', final=True),
    Key('scheme.bar.stops', 'bar', 'stops', 0, 0, (), 'live', True),
    key('scheme.bar.line', 'bar_line', 'color', final=True),
    key('scheme.bar.line2', 'bar_line2', 'color'),  # the light half draws it, final, above bar.line; 0: none
    key('scheme.mode', 'mode', 'enum'),
    # the light half's flip ends; the dark half's are fixed, #000000 and #ffffff
    Key('scheme.paper', 'paper', 'color', 0, 0xffffff, O['LIGHT_PAPER'] * 0x010101, 'live'),
    Key('scheme.ink', 'ink', 'color', 0, 0xffffff, O['LIGHT_INK'] * 0x010101, 'live'),
    Key('playing.layout', 'np_layout', 'enum', 0, 1, 0, 'open'),  # the 'open' fields: after n_opts
    *(Key(f'coverflow.{e}.{a}', f'cf.{a}', 'int', lo, hi, 0, 'open') for e, a, lo, hi in (
        ('shelf', 'top', 0, 50), ('shelf', 'x1', 0, 1023), ('shelf', 'xs', 0, 799), ('shelf', 'z1', 0, 799),
        ('shelf', 'zs', 0, 799), ('shelf', 'eye', 1, 2046), ('shelf', 'bright1', 1, 256), ('shelf', 'bright2', 1, 256),
        ('shelf', 'reflect', 1, 50), ('shelf', 'reflect_top', 0, 256), ('shelf', 'stride', 1, 375),
        ('shelf', 'radius', 0, 83), ('caption', 'name_px', 1, 32), ('caption', 'artist_px', 1, 28),
        ('caption', 'text_y', 210, 290))),
    # no payload field: tools/art.py draws these into the theme's images
    Key('bar.battery.bolt', 'bolt', 'color', 0, 0xffffff, 0, 'art'),
    Key('bar.battery.bolt_halo_a', 'bolt_halo_a', 'int', 0, 255, 0, 'art'),
    Key('bar.state.stops', 'state_stops', 'stops', 0, 0, (), 'art'),
    Key('bar.state.edge', 'state_edge', 'stops', 0, 0, (), 'art'),
    Key('bar.state.halo_light', 'state_halo_light', 'int', 0, 255, 0, 'art'),
    Key('bar.state.halo_dark', 'state_halo_dark', 'int', 0, 255, 0, 'art'),
    Key('geometry.set', 'geom_set', 'set', 0, 0, 'ipod', 'boot'))}
FIELDS = {k.field: k for k in SCHEMA.values()}


def view_attr(path):
    """Whether a SCHEMA path is a view element's attribute (list.selection.painter), not a view's own
    (playing.layout) or a scheme role (scheme.sel.stops)."""
    return path.count('.') == 2 and not path.startswith('scheme.')


ELEMENTS = {p.rpartition('.')[0] for p in SCHEMA if view_attr(p)}
VIEWS = {e.partition('.')[0] for e in ELEMENTS}
CHILDREN = {'theme': {'colorSchemes', 'colorScheme', 'geometry', 'option', 'view'}, 'view': {'element'}, 'option': {'value'}}
OPTION_ATTRS = {'setting', 'key', 'label', 'default', 'preset', 'keep', 'values'}
# Per-theme numbers stay in offsets.inc: theme id -> {C field: expression over O}.
CF_ROLES = {'cf.top': 'CF_TOP', 'cf.x1': 'CF_X1', 'cf.xs': 'CF_XS', 'cf.z1': 'CF_Z1', 'cf.zs': 'CF_ZS', 'cf.eye': 'CF_EYE',
            'cf.bright1': 'CF_BRIGHT1', 'cf.bright2': 'CF_BRIGHT2', 'cf.reflect': 'CF_REFLECT',
            'cf.reflect_top': 'CF_REFLECT_TOP', 'cf.stride': 'CF_STRIDE', 'cf.radius': 'NP_ART_RADIUS',
            'cf.name_px': 'CF_NAME_PX', 'cf.artist_px': 'CF_ARTIST_PX', 'cf.text_y': 'CF_TEXT_Y'}
ROLE_MACROS = {
    'minimal': {'home_top': 'HOME_TOP', 'home_full_row': 'HOME_FULL_ROW', 'home_text_x': 'HOME_DOT_X + HOME_DOT + HOME_DOT_GAP',
                'home_label_end[0]': 'HOME_SPLIT_LABEL_END', 'home_label_end[1]': 'HOME_LABEL_END', 'set_top': 'SET_TOP',
                'np_bar': 'NP_BAR_MINIMAL', 'np_scrub': 'NP_BAR_CLASSIC', 'batt_fill': '0',
                'fill': 'MINIMAL_FILL', 'chevron': 'MINIMAL_CHEVRON', 'muted': 'SUDO_MUTED', 'bar_light': '0', **CF_ROLES},
    # Classic: what its theme.xml doesn't state itself (Now Playing's bar, the stock battery's fill, the bars outside its gloss)
    'classic-light': {'set_top': '0', 'np_bar': 'NP_BAR_CLASSIC', 'batt_fill': 'BATT_CHARGE_RGB', 'fill': '0', 'chevron': '0',
                      'bar_light': 'LIGHT_BAR'},
    'modern': {'home_top': '0', 'home_full_row': 'HOME_MODERN_ROW', 'home_text_x': 'HOME_MODERN_TEXT_X',
               'home_label_end[0]': 'HOME_MODERN_LABEL_END', 'home_label_end[1]': 'HOME_MODERN_LABEL_END', 'set_top': '0',
               'np_bar': 'NP_BAR_CLASSIC', 'batt_fill': 'BATT_CHARGE_RGB', 'fill': '0', 'chevron': '0', 'muted': '0',
               'bar_light': 'LIGHT_BAR'},
}

# Classic Blue's selection band: its 1 px top line, the stops under it and its edge row:
# derive="gloss"'s shape. assets/themes/classic-light/theme.xml states the same values (test_theme checks).
GLOSS = ((0, 0x08a6e7), (17, 0x009ade), (38, 0x008ade), (58, 0x007dde), (79, 0x006dd6), (100, 0x0065d6))
GLOSS_TOP, GLOSS_EDGE, GLOSS_TALL_EDGE, GLOSS_TALL_LINE, GLOSS_TALL_TOP = 0x94c7de, 0x004184, 0x004aa3, 0x688db3, 0x27ade5


def turn(c, top):
    """c, a colour of Classic Blue's band, in the accent whose top tone is top: its HSV hue turned by top's
    less the band's 38 % stop's, saturation and value scaled by theirs, so that stop becomes top."""
    def hsv(x): return colorsys.rgb_to_hsv(*((x >> s & 255) / 255 for s in (16, 8, 0)))
    (h0, s0, v0), (h, s, v), (ch, cs, cv) = hsv(GLOSS[2][1]), hsv(top), hsv(c)
    rgb = colorsys.hsv_to_rgb((ch + h - h0) % 1, min(1, cs * s / s0), min(1, cv * v / v0))
    return '#' + ''.join(f'{round(255 * x):02x}' for x in rgb)


def derive_gloss(tones):
    """Classic Blue's stops in the accent of tones, an ACCENTS row: derive="gloss"."""
    return [(p, turn(c, tones[0])) for p, c in GLOSS]


GEOMETRIES = {SET0.name: SET0}  # the built-in set; a theme declares its others (t['geoms'])
GEOM_NAME = re.compile('(?!n$)[a-z][a-z0-9-]{0,23}')  # GEOM_<NAME> stays one C name per set, never GEOM_N
# Geometry field -> its inclusive (lo, hi), each an int or a function of the finished set.
GEOM_RANGES = {
    'bar': (26, 40),  # 26: Home's scratch frame (DRIFT_TEXELS, 4 x 188 x 294) holds a client of 294 at most; 40: an eighth of the screen
    'pitch': (28, 78),  # 28: the smallest row proved to fill the stock row pool at h 294; 78: stock's own row
    'body': (24, lambda g: g.pitch),  # inside its row, and tall enough for stock's 24 px title box
    'art': (0, lambda g: min(52, g.art_body)),  # stock covers are 52 px, drawn 1:1 or smaller; 0 hides them
    'art_inset': (0, lambda g: (g.art_body - g.art) // 2),  # the art and its insets fit the body
    'art_pitch': (28, 78),
    'art_body': (24, lambda g: g.art_pitch),
    'text_art': (0, lambda g: min(52, g.body)),
    'text_art_inset': (0, lambda g: (g.body - g.text_art) // 2),
    'lib_pitch': (28, 78),  # the Library's own rows
    'lib_body': (24, lambda g: g.lib_pitch),
    'lib_art': (0, lambda g: min(52, g.lib_body)),
    'lib_inset': (0, lambda g: (g.lib_body - g.lib_art) // 2),
    'title24_y': (0, lambda g: g.body - 24),  # stock's 24 px label box inside the body
    'title_y': (0, lambda g: g.body - 20),  # the 20 px box
    'meta_y': (0, lambda g: g.body - 16),  # the 16 px boxes
    'count_y': (0, lambda g: g.body - 16),
    'art_title24_y': (0, lambda g: g.art_body - 24),
    'art_meta_y': (0, lambda g: g.art_body - 16),
    'song_pitch': (28, 78),  # Songs' own rows
    'song_body': (24, lambda g: g.song_pitch),
    'song_title24_y': (0, lambda g: g.song_body - 24),
    'song_meta_y': (0, lambda g: g.song_body - 16),
    'set_row': (28, 78),
    'set_top': (0, 40),  # a short header at most under the bar
    'set_rows': (1, lambda g: (320 - g.bar - g.set_top) // g.set_row),  # the settings list fits the client
    'set_icon': (0, lambda g: g.set_row),
    'bar_px': (0, lambda g: g.bar),  # the bar title's size inside the bar; 0: no title
    'bar_title_w': (0, 188),  # its text's width: at most the bar's left half, a full-length title reaching the clock's box
    'bar_right': (0, 80),
    'batt_w': (1, 40),
    'batt_h': (1, lambda g: g.bar - 2),  # inside the bar, clear of its line row
    'text_x': (0, 60),  # the glass rule stays test/build.py's
    'action_row': (0, 50),
    'list_end': (0, lambda g: 320 - g.bar),  # ... and list_end's, inside the window
    'home_lib': (0, 1),  # Home's Library row slides in a list of the Library's rows
    'home_row': (28, lambda g: (320 - g.bar - O['HOME_TOP']) // 7),  # seven Home rows under HOME_TOP fit the client
}
THEME_GEOM_OFF = 4  # offsetof(theme_t, geom), right after name so no field moves it; asserted in navigation.c; test/patch.py pokes it
SCHEME_MODE_OFF = 5  # offsetof(scheme_t, mode) on MIPS, right after flags; asserted in navigation.c; test/patch.py pokes it


def sets(t):
    """The sets theme t can name: the built-in ipod and those it declares or inherits."""
    return {**GEOMETRIES, **t['geoms']}


def check_set(g):
    """The first field of g, in Geometry order, outside its range: its message; else None."""
    for f in dataclasses.fields(Geometry)[1:]:
        v = getattr(g, f.name)
        lo, hi = (b(g) if callable(b) else b for b in GEOM_RANGES[f.name])
        if not lo <= v <= hi:
            return f'geometry.{f.name}: {v} outside {lo}..{hi}'
    return None


def declare(node, known):
    """One <geometry name> over its extends= set (default ipod), checked, as a Geometry; known: the sets it can name."""
    def err(message): fail(node.file, node.line, message)
    name = node.attrs['name']
    if not GEOM_NAME.fullmatch(name):
        err('geometry.name: expected [a-z][a-z0-9-]*, 1..24 bytes, not n')
    if name in known:
        err(f'geometry set {name} declared twice')
    ints = {}
    for k, text in node.attrs.items():
        if k in ('name', 'extends'):
            continue
        if k not in GEOM_RANGES:
            err(f'unknown key geometry.{k}')
        if not re.fullmatch('-?[0-9]+', text):
            err(f'geometry.{k}: expected an integer')
        ints[k] = int(text)
    base = node.attrs.get('extends', SET0.name)
    if base not in known:
        err(f'unknown geometry set "{base}"')
    g = dataclasses.replace(known[base], name=name, **ints)
    message = check_set(g)
    if message:
        err(message)
    return g


def geometry(t, values=None):
    """The set t boots: its set-selecting own option's for values[key] (own key -> value label) when values
    names it, else its <geometry set>."""
    o = next((o for o in t['opts'] if o.get('sets') and o['key'] in (values or {})), None)
    return sets(t)[o['sets'][o['names'].index(values[o['key']])] if o else t['geom_set']]


def geometry_sets(themes):
    """The distinct geometry sets of themes and their own options' values, SET0 first: boot_values[]' rows."""
    found = list(dict.fromkeys([SET0, *map(geometry, themes),
                                *(sets(t)[s] for t in themes for o in t['opts'] if o.get('sets') for s in o['sets'])]))
    for g in found:
        v = immediate_values(g)
        for n, x in v.items():
            check(0 <= x < 0x8000, f'{g.name}: {n} {x} does not fit an immediate')
    return found


class ThemeError(Exception):
    pass


def fail(file, line, message):
    raise ThemeError(f'{file}:{line}: {message}')


@dataclasses.dataclass
class Node:
    tag: str
    attrs: dict
    children: list
    line: int
    file: str


# A strict XML subset: comments, elements with double-quoted lowercase attributes, and whitespace.
# WS, not \s, which also accepts \xa0 and other characters XML calls text.
WS = '[ \t\r\n]'
# Values exclude controls (etree folds \t\r\n to spaces) and comments '--' (etree rejects it).
TOKEN = re.compile(rf'<!--(?:(?!--).)*-->|<(/?)([A-Za-z][A-Za-z0-9]*)'
                   rf'((?:{WS}+[a-z0-9_.]+="[^"<\x00-\x1f]*")*){WS}*(/?)>|{WS}+', re.S)
ENTITIES = {'amp': '&', 'lt': '<', 'gt': '>', 'quot': '"'}


def tokenize(text, file):
    """(kind, tag, attrs, line) for each tag, kind 'open', 'empty' or 'close'."""
    tokens, pos = [], 0
    while pos < len(text):
        line = text.count('\n', 0, pos) + 1
        m = TOKEN.match(text, pos)
        if not m:
            rest = text[pos:]
            fail(file, line, 'unclosed comment' if rest.startswith('<!--') else
                 'malformed tag' if rest.startswith('<') else 'text between tags')
        pos = m.end()
        if m[2] is None:
            continue  # a comment or whitespace
        if m[1] and (m[3] or m[4]):
            fail(file, line, 'malformed tag')
        attrs = {}
        for name, value in re.findall(r'([a-z0-9_.]+)="([^"]*)"', m[3]):
            if name in attrs:
                fail(file, line, f'duplicate attribute {name}')
            if re.search(r'&(?!(?:amp|lt|gt|quot);)', value):
                fail(file, line, 'unknown entity')
            attrs[name] = re.sub(r'&(amp|lt|gt|quot);', lambda e: ENTITIES[e[1]], value)
        tokens.append(('close' if m[1] else 'empty' if m[4] else 'open', m[2], attrs, line))
    return tokens


def parse(text, file):
    """The root <theme format="1"> Node of a theme file's text."""
    root, stack = None, []
    for kind, tag, attrs, line in tokenize(text, file):
        if root and not stack:
            fail(file, line, 'more than one root element')
        if kind == 'close':
            if not stack or stack[-1].tag != tag:
                fail(file, line, f'mismatched </{tag}>')
            stack.pop()
            continue
        node = Node(tag, attrs, [], line, file)
        if root:
            stack[-1].children.append(node)
        elif tag != 'theme' or attrs.get('format') != '1':
            fail(file, line, 'root must be <theme format="1">')
        else:
            root = node
        if kind == 'open':
            stack.append(node)
    if stack:
        fail(file, stack[-1].line, f'unclosed <{stack[-1].tag}>')
    if not root:
        fail(file, 1, 'root must be <theme format="1">')
    return root


def load(path):
    """parse() a theme.xml, named <id>/theme.xml in errors."""
    file = f'{path.parent.name}/{path.name}'
    data = path.read_bytes()
    if len(data) > 32768:
        fail(file, 1, 'larger than 32 KB')
    try:
        text = data.decode()
    except UnicodeDecodeError:
        fail(file, 1, 'not UTF-8')
    return parse(text, file)


def zero_scheme():
    return {**{k.field: k.default for k in SCHEMA.values() if k.path.startswith('scheme.')}, 'light': {}}


def split_half(attr):
    """'light.sel.top' -> ('light', 'sel.top'); 'dark.x' -> ('dark', 'x'); 'x' -> (None, 'x')."""
    h, dot, rest = attr.partition('.')
    return (h, rest) if dot and h in ('light', 'dark') else (None, attr)


def flip(c, paper, ink):
    """navigation.c's flip(): a neutral (channels within LIGHT_SPREAD) per channel paper - v * (paper - ink) // 255
    on the low bytes of paper and ink; else c."""
    ch = [c >> s & 255 for s in (0, 8, 16)]
    if max(ch) - min(ch) > O['LIGHT_SPREAD']:
        return c
    p, i = paper & 255, ink & 255
    return sum((p - v * (p - i) // 255) << s for v, s in zip(ch, (0, 8, 16)))


def unflip(c, paper, ink):
    """flip's approximate inverse: a neutral per channel (paper - v) * 255 // (paper - ink), clamped to 0..255; else c."""
    ch = [c >> s & 255 for s in (0, 8, 16)]
    if max(ch) - min(ch) > O['LIGHT_SPREAD']:
        return c
    p, i = paper & 255, ink & 255
    return sum(min(255, max(0, (p - v) * 255 // (p - i))) << s for v, s in zip(ch, (0, 8, 16)))


FINAL = [k for k in SCHEMA.values() if k.final]


def half(d, light, paper, ink):
    """d's final fields in one half: the stated value, else the other half's flipped (light) or unflipped
    (dark); unset stays unset; stops per stop, positions kept; 'light' dropped."""
    out = {f: v for f, v in d.items() if f != 'light'}
    f_ = flip if light else unflip
    for k in FINAL:
        if k.field not in d:
            continue
        v = d['light'].get(k.field) if light else d[k.field] or None
        if v is None:  # unstated: the other half's, flipped; unset (0, ()) stays unset
            o = d[k.field] if light else d['light'].get(k.field, k.default)
            v = tuple((p, f_(c, paper, ink)) for p, c in o) if k.type == 'stops' else o and f_(o, paper, ink)
        out[k.field] = v
    return out


def scheme_half(s, light):
    """half(s, light, s['paper'], s['ink']), paper and ink the half's."""
    return {**half(s, light, s['paper'], s['ink']), **({} if light else {'paper': 0, 'ink': 0xffffff})}


def shared(t):
    """No scheme states a final value in either half: one array serves both."""
    return not any(s['light'] or any(s[k.field] for k in FINAL if k.path.startswith('scheme.')) for s in t['schemes'])


def check_tags(node):
    for c in node.children:
        if c.tag not in CHILDREN.get(node.tag, ()):
            fail(c.file, c.line, f'unknown element <{c.tag}>')
        check_tags(c)


def ranged(node, k, v):
    if not k.lo <= v <= k.hi:
        fail(node.file, node.line, f'{k.path}: {v} outside {k.lo}..{k.hi}')
    return v


def value(node, path, text, macros):
    """SCHEMA[path]'s value for an attribute's text."""
    def err(message): fail(node.file, node.line, message)
    k = SCHEMA.get(path)
    if not k:
        err(f'unknown key {path}')
    # ponytail: a final colour's 0 means unset (theme.h), so it has no black; a role needs #010101 until one wants a flag
    if k in FINAL and k.type == 'color' and text.lower() == '#000000':
        err(f'{path}: #000000 reads as unset; use #010101')
    if k.field in macros:
        err(f'{path} comes from patch/offsets.inc')
    if k.type == 'bool':
        if text not in ('0', '1'):
            err(f'{path}: expected 0 or 1')
        v = int(text)
    elif k.type == 'enum':
        names = list(CHOICES[path])
        if text not in names:
            err(f'{path}: unknown value {text}')
        v = names.index(text)
    elif k.type == 'color':
        if not re.fullmatch('#[0-9a-fA-F]{6}', text):
            err(f'{path}: expected #rrggbb')
        v = int(text[1:], 16)
    elif k.type == 'alphas':  # "N N ...": 1 to 32 alphas of 255
        if not re.fullmatch(r'[0-9]{1,3}( [0-9]{1,3}){0,31}', text) or any(int(a) > 255 for a in text.split()):
            err(f'{path}: expected up to 32 alphas 0..255')
        return tuple(map(int, text.split()))
    elif k.type == 'stops':  # "#rrggbb N, ...": 2 to 13 stops (stops_t), N rising from 0 to 100
        stops = [re.fullmatch(r'#([0-9a-fA-F]{6}) ([0-9]{1,3})', s.strip()) for s in text.split(',')]
        if not all(stops):
            err(f'{path}: expected "#rrggbb N, ..."')
        v = tuple((int(m[2]), int(m[1], 16)) for m in stops)
        if not 2 <= len(v) <= 13:
            err(f'{path}: 2 to 13 stops')
        if v[0][0] != 0 or v[-1][0] != 100 or any(a[0] >= b[0] for a, b in zip(v, v[1:])):
            err(f'{path}: positions must rise from 0 to 100')
        return v
    else:
        if not re.fullmatch('-?[0-9]+', text):
            err(f'{path}: expected an integer')
        v = int(text)
    return ranged(node, k, v)


def own_key(node, key):
    """A theme's own IPOD key: never the payload's."""
    if not re.fullmatch('[A-Z][A-Z0-9_]*', key):
        fail(node.file, node.line, 'option.key: expected [A-Z][A-Z0-9_]*')
    if key in PAYLOAD_KEYS:
        fail(node.file, node.line, f"{key}: the payload's key")
    return key


def label_ok(text):  # a row's text: 1..24 bytes, no control characters
    return 0 < len(text.encode()) <= 24 and not re.search('[\x00-\x1f\x7f-\x9f]', text)


def presets(c, o, names, what):
    """o's def, preset and keep: indices into names of c's default, preset and keep, -1 when absent."""
    for attr, field in (('default', 'def'), ('preset', 'preset'), ('keep', 'keep')):
        if attr in c.attrs:
            if c.attrs[attr] not in names:
                fail(c.file, c.line, f'{what}: unknown value {c.attrs[attr]}')
            o[field] = names.index(c.attrs[attr])
    return o


def offered(c, o, names, what):
    """c's values, the bitmask of names its row takes. A stored value outside them reads as its default, so it
    has one, and every value the option names is one of them."""
    listed = c.attrs['values'].split()
    if len(set(listed)) < 2:
        fail(c.file, c.line, f'{what}: 2 or more values expected')
    for n in listed:
        if n not in names:
            fail(c.file, c.line, f'{what}: unknown value {n}')
    if o['def'] < 0:
        fail(c.file, c.line, f'{what}: values need a default, what a stored value outside them reads as')
    for attr, field in (('default', 'def'), ('preset', 'preset'), ('keep', 'keep')):
        if o[field] >= 0 and names[o[field]] not in listed:
            fail(c.file, c.line, f'{what}: {attr} {names[o[field]]} is not in values')
    return sum(1 << names.index(n) for n in set(listed))


PLAIN_KEYS = {'HOMELIB', 'ROWPREVIEW'}  # the plain own keys the payload reads (navigation.c home_lib_on, row_preview_on)


def own_option(c, known):
    """A theme's own <option key label>: its values, each naming the geometry set it boots, one of known, or
    none of them naming one: a plain choice the payload reads as st.own[OWN_<KEY>]."""
    def err(node, message): fail(node.file, node.line, message)
    key = own_key(c, c.attrs['key'])
    if 'values' in c.attrs:
        err(c, f'{key}: its <value>s are its values')
    if not label_ok(c.attrs.get('label', '')):
        err(c, f'{key}: label expected 1..24 bytes, no control characters')
    names, sets = [], []
    for v in c.children:
        for k in v.attrs:
            if k not in ('label', 'set'):
                err(v, f'unknown key value.{k}')
        x = v.attrs.get('label', '')
        if not label_ok(x):
            err(v, f'{key}: value label expected 1..24 bytes, no control characters')
        if x in names:
            err(v, f'{key}: duplicate value {x}')
        if ('set' in v.attrs) != ('set' in c.children[0].attrs):
            err(v, f'{key}: every value names a set, or none does')
        if 'set' in v.attrs and v.attrs['set'] not in known:
            err(v, f'unknown geometry set "{v.attrs["set"]}"')
        names.append(x)
        sets.append(v.attrs.get('set'))
    if not 2 <= len(names) <= 10:  # config_value reads one digit
        err(c, f'{key}: 2 to 10 values expected')
    if not sets[0] and key not in PLAIN_KEYS:
        err(c, f'{key}: a plain choice the payload does not read (PLAIN_KEYS)')
    # ponytail: set is the one selector; a value that selects a scheme or a font adds its attribute, and its payload reader, when a theme needs it
    return presets(c, {'setting': 'SETTING_OWN', 'key': key, 'label': c.attrs['label'], 'names': names, 'sets': sets if sets[0] else None,
                       'def': -1, 'preset': -1, 'keep': -1, 'values': 0}, names, key)


def scheme_names(t):
    """The names accent values index: the ACCENTS rows, then the theme's own schemes."""
    return ACCENT_NAMES + [s['name'] for s in t['schemes'][len(ACCENT_NAMES):]]


def resolve(root, by_id, seen=()):
    """C field -> value for one theme: the defaults or its base, then its body, then ROLE_MACROS."""
    def err(node, message): fail(node.file, node.line, message)
    a = root.attrs
    for k in a:
        if k not in ('format', 'id', 'name', 'index', 'extends'):
            err(root, f'unknown key theme.{k}')
    tid = a.get('id', '')
    if not re.fullmatch('[a-z][a-z0-9_-]*', tid):
        err(root, 'id: expected [a-z][a-z0-9_-]*')
    # the Theme row's "Theme: %s" holds 63 bytes; a control character would reach C (a C1 one as \u0080)
    if not 0 < len(a.get('name', '').encode()) <= 48 or re.search('[\x00-\x1f\x7f-\x9f]', a['name']):
        err(root, 'name: expected 1..48 bytes, no control characters')
    if not re.fullmatch('[0-9]', a.get('index', '')):
        err(root, 'index: expected a digit')
    if tid in seen:
        err(root, f'extends cycle at {tid}')
    check_tags(root)
    if 'extends' in a:
        if a['extends'] not in by_id:
            err(root, f'extends unknown theme {a["extends"]}')
        t = resolve(by_id[a['extends']], by_id, seen + (tid,))  # a fresh dict: ours to change
    else:
        t = {'accents': ALL_ACCENTS, 'accent_key': None, 'schemes': [zero_scheme() for _ in ACCENT_NAMES], 'opts': [], 'geoms': {},
             **{k.field: k.default for k in SCHEMA.values() if not k.path.startswith('scheme.')}, 'light': {}}
    t.update(id=tid, name=a['name'], index=int(a['index']), line=root.line)
    macros = ROLE_MACROS.get(tid, {})
    opts = None
    named = []
    for c in root.children:
        if c.tag == 'colorSchemes':
            t['accents'] = 0
            t.pop('derive', None)
            for k in c.attrs:
                if k not in ('accents', 'derive'):
                    err(c, f'unknown key colorSchemes.{k}')
            if c.attrs.get('derive', 'gloss') != 'gloss':
                err(c, f'colorSchemes.derive: unknown value {c.attrs["derive"]}')
            if 'derive' in c.attrs:
                t['derive'] = 'gloss'
            names = scheme_names(t)
            for n in c.attrs.get('accents', '').split():
                if n not in names:
                    err(c, f'unknown scheme {n}')
                t['accents'] |= 1 << names.index(n)
        elif c.tag == 'colorScheme':
            n = c.attrs.get('name')  # none: every scheme the theme has
            own = n is not None and 'base' in c.attrs  # a scheme of the theme's own, after the ACCENTS rows
            if own:
                label = c.attrs.get('label', '')
                if n in ACCENT_NAMES:
                    err(c, f'{n}: an ACCENTS row takes no base')
                if c.attrs['base'] not in ACCENT_NAMES:
                    err(c, f'{n}: unknown base {c.attrs["base"]}')
                if not label:
                    err(c, f'{n}: an own scheme needs a label')
                if len(label.encode()) > 48 or re.search('[\x00-\x1f\x7f-\x9f]', label):
                    err(c, f'{n}: label: expected 1..48 bytes, no control characters')
                if n not in scheme_names(t):
                    b = t['schemes'][ACCENT_NAMES.index(c.attrs['base'])]
                    t['schemes'].append(dict(b, name=n, label=label, base=ACCENT_NAMES.index(c.attrs['base']),
                                             light=dict(b['light'])))
                    t['accents'] |= 1 << len(t['schemes']) - 1
            names = scheme_names(t)
            if n is not None and n not in names:
                err(c, f'unknown scheme {n}')
            if n is not None:
                named.append((c, n))
            targets = t['schemes'] if n is None else [t['schemes'][names.index(n)]]
            for attr, text in c.attrs.items():
                if attr == 'name' or own and attr in ('base', 'label'):
                    continue
                h, k = split_half(attr)
                path = f'scheme.{k}'
                x = value(c, path, text, macros)
                f = SCHEMA[path]
                if k in ('paper', 'ink'):  # the light half's flip ends; the dark half's are fixed
                    if h != 'light':
                        err(c, f"{path}: the dark half's is {'#000000' if k == 'paper' else '#ffffff'}; write light.{k}")
                elif h and not f.final:
                    err(c, f'{path}: one value for both halves')
                elif h == 'dark' and k in c.attrs:
                    err(c, f'{path}: stated twice ({k} and dark.{k})')
                for s in targets:
                    (s['light'] if h == 'light' and f.final else s)[f.field] = x
        elif c.tag == 'geometry':
            if 'name' in c.attrs:
                g = declare(c, sets(t))
                t['geoms'][g.name] = g
                continue
            for k, text in c.attrs.items():
                if k != 'set':
                    err(c, f'unknown key geometry.{k}')
                if text not in sets(t):
                    err(c, f'unknown geometry set "{text}"')
                t['geom_set'] = text
        elif c.tag == 'option':
            if opts is None:
                opts = t['opts'] = []  # this file's options replace the inherited list
            for k in c.attrs:
                if k not in OPTION_ATTRS:
                    err(c, f'unknown key option.{k}')
            if 'key' in c.attrs and 'setting' not in c.attrs:
                opts.append(own_option(c, sets(t)))
                continue
            s = c.attrs.get('setting', '')
            if s not in SETTINGS:
                err(c, f'unknown setting {s}')
            if c.children:
                err(c.children[0], f'{s}: a payload setting takes no <value>')
            if 'label' in c.attrs:  # its row's title is the payload's
                err(c, f'{s}: a payload setting takes no label')
            macro, names = SETTINGS[s]
            if s == 'accent':
                names = scheme_names(t)
            o = presets(c, {'setting': macro, 'def': -1, 'preset': -1, 'keep': -1, 'values': 0}, names, s)
            if 'values' in c.attrs:
                if s == 'accent':
                    err(c, 'accent: colorSchemes states its values')
                o['values'] = offered(c, o, names, s)
            if 'key' in c.attrs:
                if s != 'accent':
                    err(c, f'{s}: unknown key {c.attrs["key"]}')
                # ACCENT or MINIMAL_ACCENT, else a theme-owned key (theme_keys)
                t['accent_key'] = c.attrs['key'] if c.attrs['key'] in ACCENT_KEYS else own_key(c, c.attrs['key'])
            opts.append(o)
        else:  # view
            v = c.attrs.get('name', '')
            if v not in VIEWS:
                err(c, f'unknown view {v}')
            for k, text in c.attrs.items():
                if k != 'name':
                    if f'{v}.{k}' not in SCHEMA:
                        err(c, f'unknown key view.{k}')
                    t[SCHEMA[f'{v}.{k}'].field] = value(c, f'{v}.{k}', text, macros)
            for e in c.children:
                el = f'{v}.{e.attrs.get("name", "")}'
                if el not in ELEMENTS:
                    err(e, f'unknown element {el}')
                for attr, text in e.attrs.items():
                    if attr != 'name':
                        h, k = split_half(attr)
                        x = value(e, f'{el}.{k}', text, macros)
                        f = SCHEMA[f'{el}.{k}']
                        if h and not f.final:
                            err(e, f'{el}.{k}: one value for both halves')
                        if h == 'dark' and k in e.attrs:
                            err(e, f'{el}.{k}: stated twice ({k} and dark.{k})')
                        (t['light'] if h == 'light' else t)[f.field] = x
    for c, n in named:
        if not t['accents'] >> scheme_names(t).index(n) & 1:
            err(c, f'scheme {n} is not in colorSchemes')
    for field, expr in macros.items():
        k = FIELDS[field]
        v = ranged(root, k, eval(expr, {'__builtins__': {}}, O))
        if k.path.startswith('scheme.'):
            for s in t['schemes']:
                s[field] = v
        else:
            t[field] = v
    if t.get('derive'):  # the gloss in each offered ACCENTS row that states none
        for i, s in enumerate(t['schemes'][:len(ACCENT_NAMES)]):
            if t['accents'] >> i & 1 and not s['sel']:
                top = ACCENT_TONES[i][0]
                s['sel'] = tuple((p, int(h[1:], 16)) for p, h in derive_gloss(ACCENT_TONES[i]))
                # the light rims are the light half's; on black the dark half's band starts on its first stop, its
                # two top rows (a 0 would take the light half's: half()), and a tall row ends on its dark edge
                for f, g in (('sel_top', GLOSS_TOP), ('sel_tall_top', GLOSS_TALL_TOP), ('sel_tall_line', GLOSS_TALL_LINE)):
                    s['light'].setdefault(f, s[f] or int(turn(g, top)[1:], 16))
                s['sel_top'] = s['sel_top'] or s['sel'][0][1]
                s['sel_tall_top'] = s['sel_tall_top'] or s['sel'][0][1]
                s['sel_edge'] = s['sel_edge'] or int(turn(GLOSS_EDGE, top)[1:], 16)
                s['sel_tall_edge'] = s['sel_tall_edge'] or int(turn(GLOSS_TALL_EDGE, top)[1:], 16)
                s['sel_tall_line'] = s['sel_tall_line'] or s['sel_tall_edge']
                for f in ('sel', 'sel_top', 'sel_edge', 'sel_tall_edge', 'sel_tall_line', 'sel_tall_top'):  # the gloss is both halves' design (Graphite's grey band flipped would turn upside down)
                    s['light'].setdefault(f, s[f])
    t['schemes'] = [s if i >= len(ACCENT_NAMES) or t['accents'] >> i & 1 else zero_scheme() for i, s in enumerate(t['schemes'])]
    for o in t['opts']:
        if o['setting'] == 'SETTING_ACCENT':
            o['values'] = 0 if t['accents'] == ALL_ACCENTS else t['accents']
    t['undeclared'] = [m for m, _ in SETTINGS.values() if m not in [o['setting'] for o in t['opts']]]
    return t


def validate(t, name):
    """The whole-theme rules resolve() can't check per attribute."""
    def err(message): fail(name, t['line'], message)
    settings = [o.get('key') or o['setting'] for o in t['opts']]
    accent = next((o for o in t['opts'] if o['setting'] == 'SETTING_ACCENT'), None)
    if not accent:
        err('no accent option')
    if not t['accent_key']:
        err('accent: no key')
    for s in settings:
        if settings.count(s) > 1:
            err(f'{s} declared twice')
    if any(o.get('key') == t['accent_key'] for o in t['opts']):
        err(f"{t['accent_key']} declared twice")
    if not t['accents']:
        err('colorSchemes is empty')
    if len(t['schemes']) > 10:  # config_value reads one digit
        err(f"{len(t['schemes'])} accents, at most 10")
    if t['painter[PAINT_SELECTION]'] == 'SEL_STOPS' and not all(s['sel'] for i, s in enumerate(t['schemes']) if t['accents'] >> i & 1):
        err('selection: painter stops needs every offered scheme\'s sel.stops')
    if t['painter[PAINT_BATTERY]'] == 'BATT_CLASSIC_LIGHT' and not t['batt']:
        err('battery: this painter needs stops')
    for n in sorted({t['geom_set'], *(s for o in t['opts'] for s in o.get('sets') or ())}):
        g = sets(t)[n]  # the bar's own label is laid out at bar_px (tools/ipod.py status_metrics), its text drawn at font_size
        if g.bar_px and t['font_px[ROLE_BAR]'] != g.bar_px:
            err(f"bar.title.font_size {t['font_px[ROLE_BAR]']} is not set {n}'s bar_px {g.bar_px}")
    for o in t['opts']:
        if o.get('setting') != 'SETTING_ACCENT' and not o.get('key') and o['def'] >= 0 and not o.get('values'):
            err(f"{o['setting']}: default has no effect without values (settings_read holds a stock setting's default)")
    for field, word in (('def', 'default'), ('preset', 'preset')):
        if accent[field] >= 0 and not t['accents'] >> accent[field] & 1:
            err(f'accent {word} {scheme_names(t)[accent[field]]} is not in colorSchemes')
    if accent['def'] < 0 and not t['accents'] & 1:
        err('accent: slot 0 is not offered, so a missing key needs a default')
    for k in SCHEMA.values():  # every value, those from a default or a base too (coverflow divides by some)
        if k.type not in ('set', 'stops', 'alphas'):
            for v in [s[k.field] for s in t['schemes']] if k.path.startswith('scheme.') else [t[k.field], t['light'].get(k.field, k.lo)]:
                if not k.lo <= v <= k.hi:
                    err(f'{k.path}: {v} outside {k.lo}..{k.hi}')
    if t['pane_shadow_w'] > len(t['pane_shadow_a']):
        err(f"home.pane.shadow_w: {t['pane_shadow_w']} past its {len(t['pane_shadow_a'])} alphas")
    for s in t['schemes']:
        for f in ('paper', 'ink'):
            if len({s[f] >> 16, s[f] >> 8 & 255, s[f] & 255}) > 1:
                err(f'scheme.{f}: expected a grey')
        if (s['paper'] & 255) <= (s['ink'] & 255):
            err('scheme.paper: must be lighter than ink')
    if 'SETTING_LIGHT' in settings and any(s['mode'] for s in t['schemes']):
        err('light: a scheme with its own mode has no Light Mode option')
    shelf = t['cf.top'] + 160 + t['cf.reflect']  # 160: coverflow.c's ART_SIZE
    if shelf > O['CF_VIEW_H']:
        err(f'coverflow.shelf: top + 160 + reflect = {shelf} exceeds {O["CF_VIEW_H"]}')
    if t['cf.bright2'] > t['cf.bright1']:
        err(f'coverflow.shelf: bright2 {t["cf.bright2"]} exceeds bright1 {t["cf.bright1"]}')
    h = O['CF_NAME_H'] + O['CF_ARTIST_H']
    bar = geometry(t).bar
    if t['cf.text_y'] + h > 320 - bar:
        err(f'coverflow.caption: text_y + {h} = {t["cf.text_y"] + h} exceeds the client height {320 - bar}')
    chooser = [o for o in t['opts'] if o.get('sets')]
    if len(chooser) > 1:
        err('one option per theme selects the geometry set')
    for o in chooser:
        d = max(o['def'], 0)
        if o['sets'][d] != t['geom_set']:
            err(f"{o['key']}: default {o['names'][d]} boots {o['sets'][d]}, the theme's geometry is {t['geom_set']}")
        for s in o['sets']:  # theme_t has one bar; the caption check above then holds for every set
            if sets(t)[s].bar != bar:
                err(f"{o['key']}: set {s} has a {sets(t)[s].bar} px bar, the theme's is {bar}")


def accent_default(t):
    return next(o['def'] for o in t['opts'] if o['setting'] == 'SETTING_ACCENT')


def c_name(t):
    """t's id as a C identifier's start: an id may hold a hyphen."""
    return t['id'].replace('-', '_')


def theme_keys(themes):
    """The themes' own IPOD keys in read order, after the payload's thirteen: each theme's own accent key and own
    options, theme then declaration order, each key once. key -> (value count, default, the own option or None
    for an accent key)."""
    keys = {}
    for t in themes:
        for o in t['opts']:
            if o.get('key'):
                keys.setdefault(o['key'], (len(o['names']), max(o['def'], 0), o))
            elif o['setting'] == 'SETTING_ACCENT' and t['accent_key'] not in ACCENT_KEYS:
                keys.setdefault(t['accent_key'], (len(t['schemes']), max(accent_default(t), 0), None))
    return keys


def builtins(root=ROOT/'assets/themes'):
    """Every <id>/theme.xml under root, resolved and validated, sorted by index."""
    nodes = [load(p) for p in sorted(root.glob('*/theme.xml'))]
    by_id = {}
    for n in nodes:
        i = n.attrs.get('id')
        if i in by_id:
            fail(n.file, n.line, f"id {i} is {by_id[i].file}'s too")
        by_id[i] = n
    found = []
    for n in nodes:
        t = resolve(n, by_id)
        validate(t, n.file)
        if t['id'] != n.file.partition('/')[0]:
            fail(n.file, n.line, f'id {t["id"]} is not its folder name')
        found.append((t['index'], n, t))
    found.sort(key=lambda f: f[0])
    owners = {}  # a set name is one GEOM_* id and one <set>_<name>.bin prefix
    for _, n, t in found:
        for name, g in t['geoms'].items():
            owner, first = owners.setdefault(name, (t['id'], g))
            if g != first:
                fail(n.file, n.line, f"geometry set {name} is {owner}'s, with other values")
    # An accent key is one stored value, which the boot fixup forces into every sharing theme's colours.
    for (_, _, t), (_, n, u) in itertools.combinations(found, 2):
        if t['accent_key'] == u['accent_key'] and (t['accents'], accent_default(t)) != (u['accents'], accent_default(u)):
            fail(n.file, n.line, f'accent key {u["accent_key"]} is {t["id"]}\'s, with other colorSchemes or default')
    # Themes on one set switch live (theme_select), relabelling the booted one's Appearance rows: the same rows.
    def rows(t): return [o['key'] if o['setting'] == 'SETTING_OWN' else o['setting'] for o in t['opts']]
    for (_, _, t), (_, n, u) in itertools.combinations(found, 2):
        if t['geom_set'] == u['geom_set'] and rows(t) != rows(u):
            fail(n.file, n.line, f"{u['id']} runs on {t['id']}'s set, with other options")
    # An own key is one stored value too: its values and default alike; its preset is each theme's (as LIGHT's).
    def shape(e): return e[:2] + ({k: v for k, v in e[2].items() if k not in ('preset', 'keep')} if e[2] else None,)
    owners = {}
    for _, n, t in found:
        for key, e in theme_keys([t]).items():
            o = owners.setdefault(key, (t, e))
            if shape(o[1]) != shape(e):
                fail(n.file, n.line, f"{key} is {o[0]['id']}'s, with other values or default")
    for i, (index, n, _) in enumerate(found):
        if index != i:
            fail(n.file, n.line, f'index {index}, expected {i}')
    return [t for _, _, t in found]


def stops_c(v):
    """A stops value's stops_t initialiser."""
    return '{0}' if not v else '{%d, {%s}, {%s}}' % (len(v), ', '.join(str(p) for p, _ in v), ', '.join(f'0x{c:06x}' for _, c in v))


def emit(themes, out, redirects={}):
    """out/'themes.inc': the schemes, options and themes[] navigation.c includes, then the boot tables.
    redirects: ui name -> bitmask of the sets with their own <set>_<name>.bin."""
    sets = geometry_sets(themes)
    keys = list(theme_keys(themes))
    def setting(o): return f'SETTING_OWN + {keys.index(o["key"])}' if o.get('key') else o['setting']
    def gname(name): return f'GEOM_{name.upper().replace("-", "_")}'
    def c(k, v):
        if k.type == 'stops':
            return stops_c(v)
        if k.type == 'alphas':
            return '{%s}' % (', '.join(map(str, v)) or '0')
        return list(CHOICES[k.path].values())[v] if k.type == 'enum' else v
    lines = ['/* tools/theme.py wrote this from assets/themes/<id>/theme.xml; edit those. */']
    def rows(t, light):
        return [f'    {{ .tones = accents[{s.get("base", v)}], .flags = {s["card"] | s["raw_red"] << 1}, .fill = 0x{s["fill"]:06x}, '
                f'.chevron = 0x{s["chevron"]:06x}, .muted = 0x{s["muted"]:06x}, .bar_light = 0x{s["bar_light"]:06x}, '
                f'.sel = {stops_c(s["sel"])}, .sel_top = 0x{s["sel_top"]:06x}, .sel_edge = 0x{s["sel_edge"]:06x}, '
                f'.sel_tall_edge = 0x{s["sel_tall_edge"]:06x}, .sel_tall_line = 0x{s["sel_tall_line"]:06x}, .sel_tall_top = 0x{s["sel_tall_top"]:06x}, '
                f'.sel_text = 0x{s["sel_text"]:06x}, .mode = {c(SCHEMA["scheme.mode"], s["mode"])}, '
                f'.paper = 0x{s["paper"]:06x}, .ink = 0x{s["ink"]:06x}, '
                f'.bar = {stops_c(s["bar"])}, .bar_line = 0x{s["bar_line"]:06x}, .bar_line2 = 0x{s["bar_line2"]:06x}'
                + (f', .label = {json.dumps(s["label"])}' if 'label' in s else '') + ' },'
                for v, s in enumerate(scheme_half(s, light) for s in t['schemes'])]
    for t in themes:
        i, one = c_name(t), shared(t)
        # one: the light rows serve both halves (today's lines); else the dark rows, then the light ones
        lines += [f'static const scheme_t {i}_schemes[] = {{', *rows(t, one), '};',
                  f'_Static_assert(sizeof {i}_schemes / sizeof *{i}_schemes >= ACCENT_N, "one scheme per ACCENTS row, then the theme\'s own");']
        if not one:
            lines += [f'static const scheme_t {i}_light[] = {{', *rows(t, True), '};',
                      f'_Static_assert(sizeof {i}_light == sizeof {i}_schemes, "one light half per scheme");']
        lines.append(f'static const option_t {i}_opts[] = {{')
        lines += [f'    {{ .setting = {setting(o)}, .def = {o["def"]}, .preset = {o["preset"]}, .keep = {o["keep"]}, '
                  f'.values = 0x{o["values"]:x} }},' for o in t['opts']]
        lines.append('};')
    lines.append('static const theme_t themes[THEME_N] = {')
    for t in themes:
        # a final field's halves {dark, light}, flipped between the default accent's light paper and ink
        d = t['schemes'][max(accent_default(t), 0)]
        halves = [half(t, light, d['paper'], d['ink']) for light in (False, True)]
        def field(k): return '{%s, %s}' % tuple(c(k, h[k.field]) for h in halves) if k.final else c(k, t[k.field])
        # theme_t order: the live element fields, the pointers and n_opts, then the 'open' fields.
        fields = [('name', json.dumps(t['name'])),
                  *((k.field, field(k)) for k in SCHEMA.values() if k.cls == 'live' and view_attr(k.path)),
                  ('accent_slot', ACCENT_KEYS.get(t['accent_key']) or f'&st.own[{keys.index(t["accent_key"])}]'), ('accent_key', json.dumps(t['accent_key'])),
                  ('schemes', f'{c_name(t)}_schemes'), ('light', f'{c_name(t)}_schemes' if shared(t) else f'{c_name(t)}_light'),
                  ('n_schemes', len(t['schemes'])),
                  ('opts', f'{c_name(t)}_opts'), ('n_opts', len(t['opts'])),
                  ('undeclared', ' | '.join(f'(1 << {m})' for m in t['undeclared']) or 0),
                  *((k.field, c(k, t[k.field])) for k in SCHEMA.values() if k.cls == 'open'),
                  ('geom', sets.index(geometry(t)))]
        lines += ['    {', *(f'        .{k} = {v},' for k, v in fields), '    },']
    lines.append('};')
    for name, bits in redirects.items():
        check(not bits & 1, f'{name}: SET0 never redirects')
        check(len(name) + 1 + max(len(g.name) for g in sets) < 64, f'{name}: its set path exceeds ringnav_ui_load\'s 64 bytes')
    sites = [(int(a, 16), g['value']) for g in AUDIT['immediates'] if isinstance(g['value'], str) for a, _ in g['sites']]
    ui = [f'{{{json.dumps(n)}, {redirects[n]}}}' for n in sorted(redirects)] + ['{0, 0}']
    check(len(sets) <= 8, f'{len(sets)} geometry sets: ui_redirect_t.sets holds 8')
    lines += ['enum { ' + ', '.join(gname(g.name) for g in sets) + ', GEOM_N };',
              'enum { ' + ', '.join(f'G_{n.upper()}' for n in VALUE_NAMES) + ', G_N };',
              f'#define BOOT_SITES {len(sites)}', f'#define CLIENT_H_MAX {max(320 - g.bar for g in sets)}',
              # volatile: -Oz can't fold the initialisers, which theme_boot and test/patch.py read
              'const volatile unsigned short boot_values[GEOM_N][G_N] = {' + ', '.join(
                  '{' + ', '.join(str(immediate_values(g)[n]) for n in VALUE_NAMES) + '}' for g in sets) + '};',
              'enum { ' + ', '.join(f'V_{n.upper()}' for n in GEOM_VALS) + ', V_N };',
              # volatile as boot_values: test/patch.py pokes it
              'static const volatile short geom_vals[GEOM_N][V_N] = {' + ', '.join(
                  '{' + ', '.join(str(getattr(g, n)) for n in GEOM_VALS) + '}' for g in sets) + '};',
              f'#define THEME_GEOM_OFF {THEME_GEOM_OFF}',
              f'#define SCHEME_MODE_OFF {SCHEME_MODE_OFF}',
              'const boot_site_t boot_sites[BOOT_SITES] = {' + ', '.join(
                  f'{{{a:#x}, G_{v.upper()}}}' for a, v in sites) + '};',
              'const char *const geom_names[GEOM_N] = {' + ', '.join(json.dumps(g.name) for g in sets) + '};',
              'const volatile ui_redirect_t ui_redirects[] = {' + ', '.join(ui) + '};',
              *(f'#define SETTING_N_{n.upper()} {len(v)}' for n, (_, v) in SETTINGS.items()),
              *(f'#define OWN_{k} {i}' for i, k in enumerate(theme_keys(themes))),
              # last: its sets are GEOM_* names
              'const theme_key_t theme_keys[THEME_KEYS] = {', *(
                  f'    {{ .key = {json.dumps(k)}, .label = 0, .names = 0, .sets = 0, .n = {n}, .def = {d} }},' if o is None else
                  f'    {{ .key = {json.dumps(k)}, .label = {json.dumps(o["label"])}, '
                  f'.names = (const char *const[]){{{", ".join(map(json.dumps, o["names"]))}}}, ' +
                  (f'.sets = (const unsigned char[]){{{", ".join(map(gname, o["sets"]))}}}, ' if o['sets'] else '.sets = 0, ') + f'.n = {n}, .def = {d} }},'
                  for k, (n, d, o) in theme_keys(themes).items()), '};', '']
    (out/'themes.inc').write_text('\n'.join(lines))
