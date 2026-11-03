"""Built-in themes: assets/themes/<id>/theme.xml in the Q2 theme XML, read, checked and written out as
the payload's theme table (themes.inc)."""
import dataclasses
import re
from build import ROOT
from ipod import INC, O


ACCENT_NAMES = [n.lower() for n in re.findall(r'\}\s*,?\s*/\* (\w+)', re.search(r'#define ACCENTS (?:.*\\\n)*.*', INC)[0])]
ALL_ACCENTS = (1 << len(ACCENT_NAMES)) - 1
SETTINGS = {'light': ('SETTING_LIGHT', ('off', 'on', 'auto')), 'accent': ('SETTING_ACCENT', ACCENT_NAMES),
            'corners': ('SETTING_ROUNDED', ('square', 'round')), 'home': ('SETTING_HOME', ('split', 'full', 'carousel')),
            'battery': ('SETTING_BATTERY', ('icon', 'percent', 'both', 'inside')),
            'shortcut': ('SETTING_SHORTCUT', ('rockbox', 'artists', 'albums', 'favorites', 'rated_albums', 'shuffle', 'games')),
            'time_title': ('SETTING_TIMETITLE', ('off', 'on')),
            'np_layout': ('SETTING_NPCENTRED', ('ipod', 'centred'))}
ACCENT_KEYS = {'ACCENT': '&st.accent', 'MINIMAL_ACCENT': '&st.minimal_accent'}
CHOICES = {'list.selection.painter': ('card', 'bar'), 'playing.title.font': ('regular', 'bold')}


@dataclasses.dataclass(frozen=True)
class Key:
    path: str
    field: str
    type: str
    lo: int
    hi: int
    default: object
    cls: str  # 'live' | 'open' | 'boot'


def key(path, field, type, hi=None):
    hi = {'bool': 1, 'color': 0xffffff, 'enum': len(CHOICES.get(path, ())) - 1}.get(type, hi)
    return Key(path, field, type, 0, hi, 0, 'live')


# theme_t declaration order; the scheme.* keys are scheme_t's.
SCHEMA = {k.path: k for k in (
    key('list.selection.painter', 'painter[PAINT_SELECTION]', 'enum'),
    key('home.chevron.visible', 'chevron_home', 'bool'),
    key('bar.title.caps', 'caps_mark', 'bool'),
    key('home.rows.caps', 'caps_home', 'bool'),
    key('playing.album.caps', 'caps_np', 'bool'),
    key('list.icons.mono', 'icons_mono', 'bool'),
    key('list.icons.badge_muted', 'icons_badge_muted', 'bool'),
    key('list.icons.addon_mono', 'icons_addon_mono', 'bool'),
    key('playing.title.font', 'bold', 'enum'),
    key('playing.album.opacity', 'np_album_op', 'int', 255),
    key('playing.state.opacity', 'np_state_op', 'int', 255),
    key('home.rows.top', 'home_top', 'int', 375),
    key('home.rows.full_row', 'home_full_row', 'int', 375),
    key('home.rows.text_x', 'home_text_x', 'int', 375),
    key('home.rows.label_end_split', 'home_label_end[0]', 'int', 375),
    key('home.rows.label_end_full', 'home_label_end[1]', 'int', 375),
    key('settings.rows.top', 'set_top', 'int', 375),
    key('playing.scrubber.height', 'np_bar', 'int', 375),
    key('playing.scrubber.scrub_height', 'np_scrub', 'int', 375),
    key('playing.art.square', 'art_square', 'bool'),
    key('bar.battery.fill', 'batt_fill', 'color'),
    key('scheme.card', 'card', 'bool'),
    key('scheme.raw_red', 'raw_red', 'bool'),
    key('scheme.fill', 'fill', 'color'),
    key('scheme.chevron', 'chevron', 'color'),
    key('scheme.muted', 'muted', 'color'),
    key('scheme.bar_light', 'bar_light', 'color'))}
FIELDS = {k.field: k for k in SCHEMA.values()}
ELEMENTS = {p.rpartition('.')[0] for p in SCHEMA if not p.startswith('scheme.')}
VIEWS = {e.partition('.')[0] for e in ELEMENTS}
CHILDREN = {'theme': {'colorSchemes', 'colorScheme', 'option', 'view'}, 'view': {'element'}}
OPTION_ATTRS = {'setting', 'key', 'default', 'preset', 'keep'}
# Per-theme numbers stay in offsets.inc: theme id -> {C field: expression over O}.
ROLE_MACROS = {
    'minimal': {'home_top': 'HOME_TOP', 'home_full_row': 'HOME_FULL_ROW', 'home_text_x': 'HOME_DOT_X + HOME_DOT + HOME_DOT_GAP',
                'home_label_end[0]': 'HOME_SPLIT_LABEL_END', 'home_label_end[1]': 'HOME_LABEL_END', 'set_top': 'SET_TOP',
                'np_bar': 'NP_BAR_MINIMAL', 'np_scrub': 'NP_BAR_CLASSIC', 'batt_fill': '0',
                'fill': 'MINIMAL_FILL', 'chevron': 'MINIMAL_CHEVRON', 'muted': 'SUDO_MUTED', 'bar_light': '0'},
    'classic': {'home_top': '0', 'home_full_row': 'HOME_CLASSIC_ROW', 'home_text_x': 'HOME_CLASSIC_TEXT_X',
                'home_label_end[0]': 'HOME_CLASSIC_LABEL_END', 'home_label_end[1]': 'HOME_CLASSIC_LABEL_END', 'set_top': '0',
                'np_bar': 'NP_BAR_CLASSIC', 'np_scrub': 'NP_BAR_CLASSIC', 'batt_fill': 'BATT_CHARGE_RGB',
                'fill': '0', 'chevron': '0', 'muted': '0', 'bar_light': 'LIGHT_BAR'},
}


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
    return {k.field: k.default for k in SCHEMA.values() if k.path.startswith('scheme.')}


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
    if k.field in macros:
        err(f'{path} comes from patch/offsets.inc')
    if k.type == 'bool':
        if text not in ('0', '1'):
            err(f'{path}: expected 0 or 1')
        v = int(text)
    elif k.type == 'enum':
        if text not in CHOICES[path]:
            err(f'{path}: unknown value {text}')
        v = CHOICES[path].index(text)
    elif k.type == 'color':
        if not re.fullmatch('#[0-9a-fA-F]{6}', text):
            err(f'{path}: expected #rrggbb')
        v = int(text[1:], 16)
    else:
        if not re.fullmatch('-?[0-9]+', text):
            err(f'{path}: expected an integer')
        v = int(text)
    return ranged(node, k, v)


def resolve(root, by_id, seen=()):
    """C field -> value for one theme: the defaults or its base, then its body, then ROLE_MACROS."""
    def err(node, message): fail(node.file, node.line, message)
    a = root.attrs
    for k in a:
        if k not in ('format', 'id', 'name', 'index', 'extends'):
            err(root, f'unknown key theme.{k}')
    tid = a.get('id', '')
    if not re.fullmatch('[a-z][a-z0-9_]*', tid):
        err(root, 'id: expected [a-z][a-z0-9_]*')
    if not a.get('name'):
        err(root, 'name: expected a name')
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
        t = {'accents': ALL_ACCENTS, 'accent_key': None, 'schemes': [zero_scheme() for _ in ACCENT_NAMES], 'opts': [],
             **{k.field: k.default for k in SCHEMA.values() if not k.path.startswith('scheme.')}}
    t.update(id=tid, name=a['name'], index=int(a['index']), line=root.line)
    macros = ROLE_MACROS.get(tid, {})
    opts = None
    for c in root.children:
        if c.tag == 'colorSchemes':
            t['accents'] = 0
            for k in c.attrs:
                if k != 'accents':
                    err(c, f'unknown key colorSchemes.{k}')
            for n in c.attrs.get('accents', '').split():
                if n not in ACCENT_NAMES:
                    err(c, f'unknown scheme {n}')
                t['accents'] |= 1 << ACCENT_NAMES.index(n)
        elif c.tag == 'colorScheme':
            n = c.attrs.get('name', '')
            if n not in ACCENT_NAMES:
                err(c, f'unknown scheme {n}')
            for k, text in c.attrs.items():
                if k != 'name':
                    x = value(c, f'scheme.{k}', text, macros)
                    t['schemes'][ACCENT_NAMES.index(n)][SCHEMA[f'scheme.{k}'].field] = x
        elif c.tag == 'option':
            if opts is None:
                opts = t['opts'] = []  # this file's options replace the inherited list
            for k in c.attrs:
                if k not in OPTION_ATTRS:
                    err(c, f'unknown key option.{k}')
            s = c.attrs.get('setting', '')
            if s not in SETTINGS:
                err(c, f'unknown setting {s}')
            macro, names = SETTINGS[s]
            o = {'setting': macro, 'def': -1, 'preset': -1, 'keep': -1, 'values': 0}
            for attr, field in (('default', 'def'), ('preset', 'preset'), ('keep', 'keep')):
                if attr in c.attrs:
                    if c.attrs[attr] not in names:
                        err(c, f'{s}: unknown value {c.attrs[attr]}')
                    o[field] = names.index(c.attrs[attr])
            if 'key' in c.attrs:
                if s != 'accent' or c.attrs['key'] not in ACCENT_KEYS:
                    err(c, f'{s}: unknown key {c.attrs["key"]}')
                t['accent_key'] = c.attrs['key']
            opts.append(o)
        else:  # view
            v = c.attrs.get('name', '')
            if v not in VIEWS:
                err(c, f'unknown view {v}')
            for k in c.attrs:
                if k != 'name':
                    err(c, f'unknown key view.{k}')
            for e in c.children:
                el = f'{v}.{e.attrs.get("name", "")}'
                if el not in ELEMENTS:
                    err(e, f'unknown element {el}')
                for k, text in e.attrs.items():
                    if k != 'name':
                        x = value(e, f'{el}.{k}', text, macros)
                        t[SCHEMA[f'{el}.{k}'].field] = x
    for field, expr in macros.items():
        k = FIELDS[field]
        v = ranged(root, k, eval(expr, {'__builtins__': {}}, O))
        if k.path.startswith('scheme.'):
            for s in t['schemes']:
                s[field] = v
        else:
            t[field] = v
    t['schemes'] = [s if t['accents'] >> i & 1 else zero_scheme() for i, s in enumerate(t['schemes'])]
    for o in t['opts']:
        if o['setting'] == 'SETTING_ACCENT':
            o['values'] = 0 if t['accents'] == ALL_ACCENTS else t['accents']
    return t


def validate(t, name):
    """The whole-theme rules resolve() can't check per attribute."""
    def err(message): fail(name, t['line'], message)
    settings = [o['setting'] for o in t['opts']]
    accent = next((o for o in t['opts'] if o['setting'] == 'SETTING_ACCENT'), None)
    if not accent:
        err('no accent option')
    if not t['accent_key']:
        err('accent: no key')
    for s in settings:
        if settings.count(s) > 1:
            err(f'{s} declared twice')
    if not t['accents']:
        err('colorSchemes is empty')
    for field, word in (('def', 'default'), ('preset', 'preset')):
        if accent[field] >= 0 and not t['accents'] >> accent[field] & 1:
            err(f'accent {word} {ACCENT_NAMES[accent[field]]} is not in colorSchemes')


def builtins(root=ROOT/'assets/themes'):
    """Every <id>/theme.xml under root, resolved and validated, sorted by index."""
    nodes = [load(p) for p in sorted(root.glob('*/theme.xml'))]
    by_id = {n.attrs.get('id'): n for n in nodes}
    found = []
    for n in nodes:
        t = resolve(n, by_id)
        validate(t, n.file)
        if t['id'] != n.file.partition('/')[0]:
            fail(n.file, n.line, f'id {t["id"]} is not its folder name')
        found.append((t['index'], n, t))
    found.sort(key=lambda f: f[0])
    for i, (index, n, _) in enumerate(found):
        if index != i:
            fail(n.file, n.line, f'index {index}, expected {i}')
    return [t for _, _, t in found]
