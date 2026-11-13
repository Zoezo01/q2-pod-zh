"""patch/navigation.c from Display to the Library lists: the Display settings and their pages, Now Playing's
info page, touch and click dispatch, the settings and list rows' layouts, Hold Return's Now Playing, the
queue menu's checks and the Library's album and artist lists."""
import ctypes as C
import re
import struct

import pytest

import host
from conftest import NAVIGATION_SOURCE, O, Deques, Files, claim, high_halves

W_PARENT, EVT_CLICK, EVENT_TYPE, EVENT_KEY, EVENT_X = O['W_PARENT'], O['EVT_CLICK'], O['EVENT_TYPE'], O['EVENT_KEY'], O['EVENT_X']
STOP = 11
THEMES = host.tools_theme.builtins()
KEYS = list(host.tools_theme.theme_keys(THEMES))  # the themes' own IPOD keys, st.own[] in this order
MINIMAL, CLASSIC, MODERN = (next(t['index'] for t in THEMES if t['id'] == i) for i in ('minimal', 'classic-light', 'modern'))


def theme_of(index):
    return next(t for t in THEMES if t['index'] == index)


def tables(library):
    """themes.h's enums (GEOM_*, G_*, V_*) by name and its two geometry tables, as this build has them."""
    inc = (library.parent/'themes.h').read_text() + (library.parent/'themes.inc').read_text()
    names = {}
    for body in re.findall(r'enum \{ ([^}]*) \};', inc):
        names |= {n.strip(): i for i, n in enumerate(body.split(','))}
    rows = {}
    for table in ('boot_values', 'geom_vals'):
        body = host.tools_build.need(re.search(table + r'\[\w+\]\[\w+\] = \{(.*)\};', inc), f'No {table}')[1]
        rows[table] = [[int(v) for v in row.split(',')] for row in re.findall(r'\{([-\d, ]+)\}', body)]
    return names, rows


@pytest.fixture(scope='module')
def scratch(library):
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


@pytest.fixture(scope='module')
def geometry(library):
    return tables(library)


@pytest.fixture
def st(fw, scratch):
    """navigation.c's st by field: st('theme').value; st('setting_label', i=3) a pointer array's cell."""
    base = C.addressof(fw.var('st', C.c_char))
    def field(name, ctype=C.c_int, i=0):
        return ctype.from_address(base + scratch[name] + i * C.sizeof(ctype))
    return field


class Config:
    """config.ini: a key's value, or the default."""
    def __init__(self, fw, **values):
        self.values = {k.encode(): v for k, v in values.items()}
        fw.mock('toolsReadConfig', self.read)

    def read(self, path, section, key, out, default, *_):
        value = self.values.get(C.string_at(key), C.string_at(default))
        C.memmove(out, value + b'\0', len(value) + 1)


def writes(fw):
    """write_int_config's (key, value) pairs in order."""
    return [(C.string_at(a[2]).decode(), C.c_int(a[0]).value) for _, a in fw.calls('write_int_config')]


class W:
    """A widget: a zeroed block holding its box and parent, as stock's I()/P() read them."""
    def __init__(self, ui, kind, parent, x=0, y=0, w=0, h=0, name=''):
        self.block = C.create_string_buffer(0x200)
        self.addr = C.addressof(self.block)
        self.kind, self.name, self.parent, self.children = kind, name, parent, []
        self.props, self.visible, self.text = {}, 1, ''
        self.box = (x, y, w, h)
        C.c_void_p.from_address(self.addr + W_PARENT).value = parent.addr if parent else None
        if parent:
            parent.children.append(self)
        ui.at[self.addr] = self

    @property
    def box(self):
        return struct.unpack_from('<4i', self.block, 0)

    @box.setter
    def box(self, value):
        struct.pack_into('<4i', self.block, 0, *(C.c_int(v).value for v in value))

    def find(self, name):
        for c in self.children:
            if c.name == name:
                return c
            found = c.find(name)
            if found:
                return found
        return None

    def labels(self):
        """Every hscroll_label under this one, in order."""
        out = []
        for c in self.children:
            out += ([c] if c.kind == 'hscroll_label' else []) + c.labels()
        return out


class Ui:
    """AWTK's widgets as a tree of W: the constructors, lookups, props, visibility, boxes and texts the C
    calls, and the window manager with its windows in stack order (the top last)."""
    def __init__(self, fw):
        self.fw, self.at, self.strings, self.handlers = fw, {}, {}, []
        self.wm = W(self, 'window_manager', None, 0, 0, 375, 320)
        for kind in ('list_item', 'button', 'image', 'hscroll_label', 'label', 'list_view', 'scroll_view', 'view'):
            if f'{kind}_create' in fw.stubs:
                fw.mock(f'{kind}_create', lambda parent, x, y, w, h, *_, k=kind: self.new(k, parent, x, y, w, h))
        fw.mock('window_manager', lambda *_: self.wm.addr)
        fw.mock('window_manager_get_top_window', lambda *_: self.wm.children[-1].addr if self.wm.children else 0)
        fw.mock('widget_get_type', lambda w, *_: self.cstr(self.at[w].kind))
        fw.mock('widget_count_children', lambda w, *_: len(self.at[w].children) if w else 0)
        fw.mock('widget_get_child', lambda w, i, *_: self.at[w].children[i].addr if w and 0 <= i < len(self.at[w].children) else 0)
        fw.mock('widget_lookup', self.lookup)
        fw.mock('widget_get_prop_str', self.prop_str)
        fw.mock('widget_set_prop_str', lambda w, name, v, *_: self.at[w].props.__setitem__(C.string_at(name).decode(), C.string_at(v)))
        fw.mock('widget_get_prop_int', lambda w, name, d, *_: self.at[w].props.get(C.string_at(name).decode(), C.c_int(d).value) if w else C.c_int(d).value)
        fw.mock('widget_set_prop_int', lambda w, name, v, *_: self.at[w].props.__setitem__(C.string_at(name).decode(), C.c_int(v).value) if w else None)
        fw.mock('widget_get_visible', lambda w, *_: self.at[w].visible if w else 0)
        fw.mock('widget_set_visible', lambda w, v, *_: setattr(self.at[w], 'visible', int(bool(v))) if w else None)
        fw.mock('widget_move_resize', lambda w, x, y, ww, h, *_: setattr(self.at[w], 'box', (x, y, ww, h)))
        fw.mock('widget_resize', lambda w, ww, h, *_: setattr(self.at[w], 'box', self.at[w].box[:2] + (ww, h)))
        fw.mock('widget_set_text_utf8', lambda w, s, *_: setattr(self.at[w], 'text', C.string_at(s).decode()) if w else None)
        fw.mock('widget_set_tr_text', lambda w, s, *_: setattr(self.at[w], 'text', C.string_at(s).decode()) if w else None)
        fw.mock('widget_get_text_utf8', self.get_text)
        fw.mock('widget_set_name', lambda w, s, *_: setattr(self.at[w], 'name', C.string_at(s).decode()) if w else None)
        fw.mock('widget_use_style', lambda w, s, *_: self.at[w].props.__setitem__('style', C.string_at(s).decode()) if w else None)
        fw.mock('image_base_set_image', lambda w, s, *_: self.at[w].props.__setitem__('image', C.string_at(s)) if w else None)
        fw.mock('widget_destroy_children', lambda w, *_: self.at[w].children.clear())
        fw.mock('widget_destroy', lambda w, *_: self.at[w].parent.children.remove(self.at[w]) if w else None)
        fw.mock('widget_on', lambda w, evt, fn, ctx, *_: self.handlers.append((w, evt, fn, ctx)) or len(self.handlers))
        fw.mock('widget_restack', self.restack)
        fw.mock('widget_to_local', self.to_local)

    def new(self, kind, parent, x, y, w, h, name=''):
        return W(self, kind, self.at[parent] if parent else None, x, y, w, h, name).addr

    def add(self, kind, parent=None, box=(0, 0, 0, 0), name='', **props):
        x, y, width, h = box
        w = W(self, kind, parent, x, y, width, h, name)
        w.props.update(props)
        return w

    def window(self, name, box=(0, 0, 375, 320)):
        """A window opened over the others."""
        return self.add('window', self.wm, box, name)

    def cstr(self, s):
        s = s.encode() if isinstance(s, str) else s
        if s not in self.strings:
            self.strings[s] = C.create_string_buffer(s)
        return C.addressof(self.strings[s])

    def lookup(self, w, name, recursive, *_):
        if not w:
            return 0
        name = C.string_at(name).decode()
        if recursive:
            found = self.at[w].find(name)
        else:
            found = next((c for c in self.at[w].children if c.name == name), None)
        return found.addr if found else 0

    def prop_str(self, w, name, default, *_):
        name = C.string_at(name).decode()
        widget = self.at.get(w)
        if widget is None:
            return default
        if name == 'name':
            return self.cstr(widget.name) if widget.name else default
        value = widget.props.get(name)
        return self.cstr(value) if isinstance(value, (bytes, str)) else default

    def get_text(self, w, out, n, *_):
        s = self.at[w].text.encode()[:n - 1]
        C.memmove(out, s + b'\0', len(s) + 1)
        return 0

    def restack(self, w, i, *_):
        widget = self.at[w]
        siblings = widget.parent.children
        siblings.remove(widget)
        siblings.insert(min(i, len(siblings)), widget)

    def to_local(self, w, xy, *_):
        x = y = 0
        widget = self.at[w]
        while widget and widget is not self.wm:
            x, y = x + widget.box[0], y + widget.box[1]
            widget = widget.parent
        point = (C.c_int * 2).from_address(xy)
        point[0] -= x
        point[1] -= y

    def clicks(self, widget):
        """widget's EVT_CLICK handlers, called as stock would: (ctx, event)."""
        return [lambda e=0, f=fn, c=ctx: C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(f)(c, e)
                for w, evt, fn, ctx in self.handlers if w == widget.addr and evt == EVT_CLICK]

    def click(self, widget):
        (call,) = self.clicks(widget)
        return call()


def row_of(label):
    """A Display row's reading: its title and value (the value label's text, the switch's On or Off, None)."""
    button = label.parent
    value = button.find('_setting_value')
    switch = button.find('_setting_switch')
    if value:
        return label.text, value.text
    if switch:
        return label.text, {b'switch_on': 'On', b'switch_off': 'Off'}[switch.props['image']]
    return label.text, None


def rows(view):
    """view's rows (list_item > button > label) as row_of reads them, in order; stock's own rows are None."""
    out = []
    for item in view.children:
        labels = [l for l in item.labels() if l.name != '_setting_value']
        out.append(row_of(labels[0]) if labels else None)
    return out


def button_of(view, title):
    """The button of view's row titled title (its text up to any ': ')."""
    for item in view.children:
        for label in item.labels():
            if label.name != '_setting_value' and label.text.split(': ')[0] == title:
                return label.parent
    raise KeyError(title)


def event(kind, key=0, x=None, y=None):
    e = C.create_string_buffer(0x40)
    struct.pack_into('<i', e, EVENT_TYPE, kind)
    if x is None:
        struct.pack_into('<i', e, EVENT_KEY, key)
    else:
        struct.pack_into('<ii', e, EVENT_X, x, y)
    return e


# The Display settings.

class Display:
    """Settings' Display page as stock opens it: navigator_to builds the window and its four stock rows, Show
    Navigation Bar last (stock_display_trampoline), and runs ringnav_display, as systemset_display_page_init does; display_open's
    sub-pages open the same way, over it."""
    def __init__(self, fw, ui):
        self.fw, self.ui, self.pages = fw, ui, []
        self.display = fw.fn('ringnav_display', C.c_int, C.c_void_p, C.c_void_p)
        fw.mock('stock_display_trampoline', lambda win, *_: self.stock(win))
        fw.mock('navigator_to', lambda name, *_: self.open(C.string_at(name).decode()))

    def stock(self, win):
        view = self.ui.at[win].find('scroll_view_display')
        for i in range(4):
            self.ui.add('list_item', view).name = f'stock_{i}'
        return 0

    def open(self, name):
        if name != 'systemset/display_page':
            return 0
        win = self.ui.window('display_page')
        nav = self.ui.add('view', win, name='view_navbar')
        self.ui.add('label', nav, name='title')
        self.ui.add('scroll_view', win, (0, 30, 375, 290), 'scroll_view_display')
        self.display(win.addr, None)
        self.pages.append(win)
        return 0

    def view(self):
        return self.pages[-1].find('scroll_view_display')

    def title(self):
        return self.pages[-1].find('view_navbar').children[0].text


def display(fw, ui, **config):
    Config(fw, **config)
    page = Display(fw, ui)
    page.open('systemset/display_page')
    return page


def test_display_adds_smart_awake_night_shift_and_its_two_pages_among_stocks_rows(fw):
    ui = Ui(fw)
    page = display(fw, ui)
    view = page.view()
    # stock's first two, then Smart Awake and Night Shift, stock's third, then Appearance and Cover Flow; its fourth, Show
    # Navigation Bar, is not offered
    assert titles(view) == ['stock_0', 'stock_1', 'Smart Awake', 'Night Shift', 'stock_2', 'Appearance', 'Cover Flow']
    # Night Shift and the two pages open a page, so they carry the chevron; Smart Awake changes in place
    into = [any(c.props.get('image') == b'list_into' for c in item.children[0].children) for item in view.children if not item.name]
    assert into == [False, True, True, True]


def titles(view):
    """Each row's title (a stock row's name), its text up to any ': '."""
    return [item.name or item.labels()[0].text.split(': ')[0] for item in view.children]


def appearance(fw, ui, **config):
    """Display, then its Appearance row: the sub-page over it."""
    page = display(fw, ui, **config)
    ui.click(button_of(page.view(), 'Appearance'))
    return page


# The payload's Appearance rows (setting_text, setting_keys[]): a setting's title, its stored key, and whether it is a
# switch (list_toggle's On or Off). A theme's own option brings its own label and key, its accent the theme's key.
PAYLOAD = {'SETTING_LIGHT': ('Light Mode', 'LIGHT', False), 'SETTING_ACCENT': ('Accent', None, False),
           'SETTING_ROUNDED': ('Rounded Corners', 'ROUNDED', True), 'SETTING_HOME': ('Home', 'HOME', False),
           'SETTING_SHORTCUT': ('Shortcut', 'SHORTCUT', False), 'SETTING_BATTERY': ('Battery', 'BATTERY', False),
           'SETTING_TIMETITLE': ('Time in Title', 'TIMETITLE', True),
           'SETTING_NPCENTRED': ('Centred Now Playing', 'NPCENTRED', True)}
# st's field for each stored key a theme's preset writes
STATE = {'THEME': 'theme', 'LIGHT': 'light', 'ACCENT': 'accent', 'ROUNDED': 'rounded', 'HOME': 'home', 'SHORTCUT': 'shortcut',
         'BATTERY': 'battery', 'TIMETITLE': 'time_title', 'NPCENTRED': 'np_centred'}


def appearance_titles(t):
    """Appearance's rows under theme t: Theme, then t's options in its theme.xml's order."""
    return ['Theme'] + [o['label'] if o['setting'] == 'SETTING_OWN' else PAYLOAD[o['setting']][0] for o in t['opts']]


def stored_key(t, o):
    if o['setting'] == 'SETTING_OWN':
        return o['key']
    return t['accent_key'] if o['setting'] == 'SETTING_ACCENT' else PAYLOAD[o['setting']][1]


def preset(t, setting):
    return next(o['preset'] for o in t['opts'] if o['setting'] == setting)


def accent_name(t, v):
    """Accent value v's name: an ACCENTS row's, else the theme's own scheme's label."""
    names = host.tools_theme.ACCENT_NAMES
    return names[v].capitalize() if v < len(names) else t['schemes'][v]['label']


def value_name(setting, v):
    """A payload setting's value v as its row reads it: tools/theme.py's name for it (Light Mode's on: On)."""
    return {s: names for s, names in host.tools_theme.SETTINGS.values()}[setting][v].capitalize()


def preset_writes(t, stored):
    """What choosing t saves after THEME, over the stored values (key -> value): each option's preset, in t's order,
    where it changes the value and the value isn't the option's keep (Light Mode's Auto)."""
    out = []
    for o in t['opts']:
        key = stored_key(t, o)
        if o['preset'] >= 0 and stored[key] not in (o['preset'], o['keep']):
            out.append((key, o['preset']))
    return out


def readings(view):
    """view's rows as {title: value}: the value label's or the switch's reading, else the text after ': '."""
    out = {}
    for row in rows(view):
        if row:
            title, _, after = row[0].partition(': ')
            out[title] = row[1] if row[1] is not None else after
    return out


def test_appearance_lists_theme_then_minimals_options_in_its_order(fw):
    ui = Ui(fw)
    page = appearance(fw, ui)
    t = theme_of(MINIMAL)
    assert len(page.pages) == 2 and page.title() == 'Appearance'
    assert titles(page.view()) == appearance_titles(t)
    # no set_text: a row reads "Title: value", a switch its On or Off
    switches = [PAYLOAD[o['setting']][0] for o in t['opts'] if o['setting'] in PAYLOAD and PAYLOAD[o['setting']][2]]
    assert switches and [row[0] for row in rows(page.view()) if row[1] is not None] == switches
    read = readings(page.view())
    # the running theme's name, and Accent at Minimal's own default, under its own key
    assert read['Theme'] == t['name'] and read['Accent'] == accent_name(t, host.tools_theme.accent_default(t))
    # nothing stored: the payload's own defaults (dark, Minimal round, Split, the clock in the title); no Rockbox: Favorites
    assert (read['Light Mode'], read['Rounded Corners'], read['Home'], read['Shortcut'], read['Time in Title']) == \
        ('Off', 'On' if t['name'] == 'Minimal' else 'Off', 'Split', 'Favorites', 'On')


def test_classic_appearance_rows_read_title_and_value_and_its_own_options(fw):
    ui = Ui(fw)
    page = appearance(fw, ui, THEME=b'%d' % CLASSIC)
    t = theme_of(CLASSIC)
    # set_text: each row its title, the value in its own right-hand label (a switch's On or Off too)
    assert [row[0] for row in rows(page.view())] == appearance_titles(t)
    read = dict(rows(page.view()))
    # Accent: the theme's own scheme, its default
    assert read['Theme'] == t['name'] and read['Accent'] == accent_name(t, host.tools_theme.accent_default(t))
    own = [o for o in t['opts'] if o['setting'] == 'SETTING_OWN']
    assert own and [read[o['label']] for o in own] == [o['names'][o['def']] for o in own]  # each at its default
    # nothing stored: the payload's own defaults, Battery's Icon one of Classic's two
    assert (read['Light Mode'], read['Home'], read['Shortcut'], read['Battery'], read['Time in Title']) == \
        ('Off', 'Split', 'Favorites', 'Icon', 'On')


def clicks_of(fw, ui, page, title, n):
    """title's row clicked n times: its reading after each and what each click wrote."""
    seen = []
    for _ in range(n):
        before = len(writes(fw))
        ui.click(button_of(page.view(), title))
        label = button_of(page.view(), title).labels()[0]
        seen.append((row_of(label)[1] or label.text.partition(': ')[2], writes(fw)[before:]))
    return seen


# Accent's stored values are its ACCENTS rows, in order: tools/theme.py names them from the same rows
ACCENT_VALUE = {name.capitalize(): i for i, name in enumerate(host.tools_theme.ACCENT_NAMES)}


def test_minimal_accent_runs_graphite_to_green_then_its_ivory(fw):
    ui = Ui(fw)
    page = appearance(fw, ui, MINIMAL_ACCENT=b'0')
    seen = clicks_of(fw, ui, page, 'Accent', 9)
    order = ['Crimson', 'Tidal', 'Champagne', 'Cobalt', 'Tangerine', 'Plum', 'Green', 'Ivory', 'Graphite']
    assert seen == [(v, [('MINIMAL_ACCENT', ACCENT_VALUE[v])]) for v in order]


def test_modern_accent_skips_ivory(fw):
    ui = Ui(fw)
    page = appearance(fw, ui, THEME=b'2', ACCENT=b'5')
    assert [v for v, _ in clicks_of(fw, ui, page, 'Accent', 4)] == ['Plum', 'Green', 'Graphite', 'Crimson']
    assert [k for k, _ in writes(fw)] == ['ACCENT'] * 4


def test_classic_accent_takes_classic_blue_after_green_and_skips_ivory(fw):
    ui = Ui(fw)
    page = appearance(fw, ui, THEME=b'1', CLASSIC_LIGHT_ACCENT=b'6')
    assert [v for v, _ in clicks_of(fw, ui, page, 'Accent', 4)] == ['Green', 'Classic Blue', 'Graphite', 'Crimson']
    assert [k for k, _ in writes(fw)] == ['CLASSIC_LIGHT_ACCENT'] * 4


def test_shortcut_cycles_in_its_menu_order(fw):
    ui = Ui(fw)
    page = appearance(fw, ui, SHORTCUT=b'0')
    seen = clicks_of(fw, ui, page, 'Shortcut', 10)
    # Rockbox, Favorites, Shuffle, Rated Albums, Playlists, Artists, Albums, Games, Podcasts, Audiobooks, Rockbox
    assert [v for v, _ in seen] == ['Favorites', 'Shuffle', 'Rated Albums', 'Playlists', 'Artists', 'Albums', 'Games',
                                    'Podcasts', 'Audiobooks', 'Rockbox']
    assert [w for _, w in seen] == [[('SHORTCUT', v)] for v in (3, 5, 4, 7, 1, 2, 6, 8, 9, 0)]


def test_home_cycles_split_full_carousel(fw):
    ui = Ui(fw)
    page = appearance(fw, ui)
    assert clicks_of(fw, ui, page, 'Home', 3) == [('Full', [('HOME', 1)]), ('Carousel', [('HOME', 2)]), ('Split', [('HOME', 0)])]


def test_classic_battery_takes_only_its_two_styles(fw):
    ui = Ui(fw)
    page = appearance(fw, ui, THEME=b'1')
    assert clicks_of(fw, ui, page, 'Battery', 2) == [('Percent Inside', [('BATTERY', 3)]), ('Icon', [('BATTERY', 0)])]


@pytest.fixture
def clock(fw):
    """time and localtime_r: hour is what the clock says."""
    state = {'hour': 12}
    def local(now, out, *_):
        C.c_int.from_address(out + 8).value = state['hour']
        return out
    fw.mock('fw_time', lambda *_: 1700000000)
    fw.mock('fw_localtime_r', local)
    return state


@pytest.mark.parametrize('hour, day', [(12, 1), (22, 0)])
def test_light_modes_auto_applies_from_its_click(fw, st, clock, hour, day):
    ui = Ui(fw)
    page = appearance(fw, ui, LIGHT=b'1')
    clock['hour'] = hour
    st('light_day').value = 1 - day
    assert clicks_of(fw, ui, page, 'Light Mode', 1) == [('Auto', [('LIGHT', 2)])]
    assert st('light_day').value == day


def repaints(fw):
    """Cached images dropped, and repaints asked."""
    return len(fw.calls('image_manager_unload_all')), len(fw.calls('widget_invalidate_force'))


@pytest.mark.parametrize('theme', [MINIMAL, MODERN])
def test_corners_drop_the_art_cache_only_where_art_takes_them(fw, theme):
    ui = Ui(fw)
    page = appearance(fw, ui, THEME=b'%d' % theme)
    before = repaints(fw)
    assert clicks_of(fw, ui, page, 'Rounded Corners', 1)[0][1] == [('ROUNDED', int(theme != MINIMAL))]  # Minimal's starts round
    after = repaints(fw)
    # a theme drawing its art square bakes the corners into its thumbnails: those cached images go
    unload = 1 if theme_of(theme)['art_square'] else 0
    assert after[0] - before[0] == unload and after[1] > before[1]  # the screen repaints either way


@pytest.mark.parametrize('title, key', [('Time in Title', 'TIMETITLE'), ('Centred Now Playing', 'NPCENTRED')])
def test_a_switch_from_smart_awake_on_redraws_only_its_toggle(fw, title, key):
    ui = Ui(fw)
    page = appearance(fw, ui)
    label = button_of(page.view(), title).labels()[0]
    was = row_of(label)[1]
    before = repaints(fw)
    ui.click(label.parent)
    assert row_of(label)[1] == {'On': 'Off', 'Off': 'On'}[was]
    assert writes(fw) == [(key, int(was == 'Off'))] and repaints(fw) == before


def test_accent_repaints_and_drops_the_cached_images(fw):
    ui = Ui(fw)
    page = appearance(fw, ui)
    before = repaints(fw)
    clicks_of(fw, ui, page, 'Accent', 1)
    after = repaints(fw)
    assert after[0] == before[0] + 1 and after[1] > before[1]


def test_library_on_home_toggles_without_a_restart(fw, st, geometry):
    names, _ = geometry
    st('geom').value = names['GEOM_CLASSIC_LIGHT']
    ui = Ui(fw)
    page = appearance(fw, ui, THEME=b'1')
    assert clicks_of(fw, ui, page, 'Library on Home', 2) == [('Off', [('HOMELIB', 0)]), ('On', [('HOMELIB', 1)])]
    assert not fw.calls('navigator_to_with_context')


def test_status_bar_steps_its_looks_and_repaints_the_bar_without_a_restart(fw, st, geometry):
    names, _ = geometry
    st('geom').value = names['GEOM_CLASSIC_LIGHT']
    ui = Ui(fw)
    page = appearance(fw, ui, THEME=b'1')
    st('pct_digit').value = 9
    before = repaints(fw)
    assert clicks_of(fw, ui, page, 'Status Bar', 4) == [('Minimal', [('STATUSBAR', 1)]), ('Modern', [('STATUSBAR', 2)]),
                                                        ('Classic', [('STATUSBAR', 3)]), ('Theme', [('STATUSBAR', 0)])]
    assert not fw.calls('navigator_to_with_context') and st('pct_digit').value == 0  # the digits, measured again
    after = repaints(fw)
    assert after[0] == before[0] and after[1] == before[1] + 4  # the bar alone, no art dropped


class Prompts:
    """navigator_to_with_context's dialogs: (page, kind, ms, text); answer is what the confirm returns (10 OK)."""
    def __init__(self, fw, answer=0):
        self.seen, self.answer = [], answer
        fw.mock('navigator_to_with_context', self.show)

    def show(self, name, info, *_):
        kind, ms = struct.unpack('<ii', C.string_at(info, 8))
        self.seen.append((C.string_at(name).decode(), kind, ms, C.string_at(info + 8).decode()))
        return self.answer


@pytest.mark.parametrize('answer', [0, 10])
def test_row_art_needs_a_restart_into_its_other_set(fw, st, geometry, answer):
    names, _ = geometry
    st('geom').value = names['GEOM_CLASSIC_LIGHT']  # Row Art On's set, as booted
    ui = Ui(fw)
    page = appearance(fw, ui, THEME=b'1')
    prompts = Prompts(fw, answer)
    label = button_of(page.view(), 'Row Art').labels()[0]
    ui.click(label.parent)
    assert prompts.seen == [('dialog/confirminfo_dialog', 1, 0, 'Restart to apply?')]
    # the running state keeps the booted set's value either way; confirmed, the config takes Off and the Q2 restarts
    assert row_of(label) == ('Row Art', 'On') and st('own', i=KEYS.index('ROWART')).value == 1
    assert writes(fw) == ([('ROWART', 0)] if answer else [])
    assert len(fw.calls('stock_reboot_trampoline')) == (answer == 10)


def themes_page(fw, ui, page):
    """Appearance's Theme row: the picker over it."""
    ui.click(button_of(page.view(), 'Theme'))
    return page.pages[-1]


def picks(view):
    return [(item.labels()[0].text, item.children[0].children[0].props['image'].decode()) for item in view.children]


def test_the_theme_picker_lists_minimal_modern_classic_marking_the_running_one(fw):
    ui = Ui(fw)
    page = appearance(fw, ui, THEME=b'2')
    themes_page(fw, ui, page)
    assert page.title() == 'Theme'
    assert picks(page.view()) == [('Minimal', 'unselect'), ('Modern', 'select'), ('Classic', 'unselect')]


def pick(ui, page, t):
    """The Theme picker's row for theme t, clicked."""
    (item,) = [item for item in page.view().children if item.labels()[0].text == t['name']]
    ui.click(item.children[0])


def test_choosing_a_theme_puts_status_bar_back_on_theme(fw, st):
    ui = Ui(fw)
    page = appearance(fw, ui, STATUSBAR=b'3')
    themes_page(fw, ui, page)
    pick(ui, page, theme_of(MODERN))
    assert ('STATUSBAR', 0) in writes(fw) and st('own', i=KEYS.index('STATUSBAR')).value == 0


def test_picking_modern_applies_its_preset_live(fw, st):
    modern = theme_of(MODERN)
    # Minimal running dark on Full, the clock in the title, its corners already Modern's
    stored = {'LIGHT': 0, 'ACCENT': 0, 'ROUNDED': preset(modern, 'SETTING_ROUNDED'), 'HOME': 1, 'TIMETITLE': 1, 'STATUSBAR': 0}
    ui = Ui(fw)
    page = appearance(fw, ui, **{k: b'%d' % v for k, v in stored.items()})
    under = page.view()
    themes_page(fw, ui, page)
    pick(ui, page, modern)
    saved = writes(fw)
    # THEME, then Modern's preset where it changes what is stored: only what differs is saved
    assert saved[0] == ('THEME', MODERN) and saved[1:] == preset_writes(modern, stored) and saved[1:]
    assert 'ROUNDED' not in dict(saved)
    # live: the running state is what was saved, and the picker marks Modern
    assert [(k, st(STATE[k]).value) for k, _ in saved] == saved
    assert [name for name, image in picks(page.view()) if image == 'select'] == [modern['name']]
    # Appearance, under the picker, reads the new theme
    read = readings(under)
    assert list(read) == appearance_titles(modern)
    assert (read['Theme'], read['Light Mode'], read['Accent'], read['Time in Title']) == (
        modern['name'], value_name('SETTING_LIGHT', preset(modern, 'SETTING_LIGHT')),
        accent_name(modern, preset(modern, 'SETTING_ACCENT')), value_name('SETTING_TIMETITLE', preset(modern, 'SETTING_TIMETITLE')))
    assert not fw.calls('navigator_to_with_context')


def test_picking_the_running_theme_changes_nothing(fw):
    ui = Ui(fw)
    page = appearance(fw, ui)
    themes_page(fw, ui, page)
    pick(ui, page, theme_of(MINIMAL))
    assert writes(fw) == [] and not fw.calls('image_manager_unload_all')


@pytest.mark.parametrize('answer', [0, 10])
def test_picking_classic_asks_to_restart_and_saves_its_preset_alone(fw, st, answer):
    classic = theme_of(CLASSIC)
    # Light Mode on Auto, Home on Carousel, the clock in the title, the themes' own keys at their defaults
    stored = {'LIGHT': 2, 'HOME': 2, 'TIMETITLE': 1} | {k: d for k, (_, d, _) in host.tools_theme.theme_keys(THEMES).items()}
    ui = Ui(fw)
    page = appearance(fw, ui, **{k: b'%d' % v for k, v in stored.items()})
    themes_page(fw, ui, page)
    prompts = Prompts(fw, answer)
    pick(ui, page, classic)
    assert [p[3] for p in prompts.seen] == ['Restart to apply?']
    # confirmed, THEME and what Classic's preset changes are saved; Light Mode's Auto is kept
    saved = writes(fw)
    assert saved == ([('THEME', CLASSIC)] + preset_writes(classic, stored) if answer else [])
    assert 'LIGHT' not in dict(saved) and bool(saved[1:]) == bool(answer)
    # the running state stays Minimal's until the restart
    assert (st('theme').value, st('home').value, st('light').value) == (MINIMAL, 2, 2)
    assert len(fw.calls('stock_reboot_trampoline')) == (answer == 10)


def test_a_new_theme_moves_a_cached_settings_list_to_its_top(fw, st, geometry):
    names, rows_ = geometry
    ui = Ui(fw)
    page = appearance(fw, ui)
    set_row = rows_['geom_vals'][st('geom').value][names['V_SET_ROW']]
    tops = {t['index']: t['set_top'] for t in THEMES}
    cached = ui.add('list_view', page.pages[0], (0, tops[MINIMAL], 375, 290))
    struct.pack_into('<i', cached.block, O['LIST_DEFAULT_ITEM_HEIGHT'], set_row)
    other = ui.add('list_view', page.pages[0], (0, tops[MINIMAL], 375, 290))
    struct.pack_into('<i', other.block, O['LIST_DEFAULT_ITEM_HEIGHT'], set_row + 4)
    assert tops[MINIMAL] != tops[MODERN]
    themes_page(fw, ui, page)
    pick(ui, page, theme_of(MODERN))
    assert cached.box[1] == tops[MODERN] and other.box[1] == tops[MINIMAL]


def test_cover_flow_page_holds_artist_view_playlists_sort_refresh_and_large_library_mode(fw):
    ui = Ui(fw)
    page = display(fw, ui)
    ui.click(button_of(page.view(), 'Cover Flow'))
    assert page.title() == 'Cover Flow'
    found = titles(page.view())
    assert len(found) == 5 and found[:2] == ['Artist View', 'Playlist View'] and found[2].startswith('Cover Flow ')
    assert found[3:] == ['Refresh Cover Flow', 'Large Library Mode']


class Settings:
    """Settings' System page: its stock rows (0-8: Language, Wireless, Network, Display, Power, Wheel Function, In-Vehicle,
    General, System) and what ringnav_systemset adds."""
    def __init__(self, fw, ui, keytone=0):
        self.win = ui.window('systemset_page')
        self.view = ui.add('scroll_view', self.win, name='scroll_view_sysset')
        for i in range(9):
            ui.add('list_item', self.view, name=f'stock_{i}')
        fw.var('g_keytone_flag', C.c_ubyte).value = keytone
        fw.fn('ringnav_systemset', C.c_int, C.c_void_p, C.c_void_p)(self.win.addr, None)


def test_system_settings_adds_its_rows_and_hides_four(fw):
    ui = Ui(fw)
    Config(fw)
    page = Settings(fw, ui)
    assert titles(page.view) == ['stock_1', 'stock_2', 'stock_3', 'Controls', 'stock_4', 'system_time', 'stock_0', 'App Store',
                                 'system_update', 'system_reset', 'stock_5', 'stock_6', 'stock_7', 'stock_8', 'system_about']
    # stock's Wheel Function, In-Vehicle, General and System
    assert [item.name for item in page.view.children if not item.visible] == ['stock_5', 'stock_6', 'stock_7', 'stock_8']
    # the rows stock moved to its System page open stock's pages from here
    opened = []
    fw.mock('navigator_to', lambda name, *_: opened.append(C.string_at(name).decode()))
    for key in ('system_time', 'system_update', 'system_reset', 'system_about'):
        ui.click(button_of(page.view, key))
    assert opened == ['systemset/datetime_page', 'systemset/fwupdate_page', 'systemset/reset_page', 'systemset/about_page']


@pytest.mark.parametrize('keytone', [0, 1])
def test_controls_opens_display_as_its_own_page(fw, keytone):
    ui = Ui(fw)
    Config(fw)
    settings = Settings(fw, ui, keytone)
    page = Display(fw, ui)
    ui.click(button_of(settings.view, 'Controls'))
    assert page.title() == 'Controls'
    found = titles(page.view())
    assert found[:3] == ['Wheel Sensitivity', 'Hold Return', 'Single Press Scrub']
    assert 'Click With Headphones' in found and 'system_keylock' in found
    # Click With Headphones shows only while Key Tone is on
    assert button_of(page.view(), 'Click With Headphones').parent.visible == keytone
    # Key Lock opens stock's page
    opened = []
    fw.mock('navigator_to', lambda name, *_: opened.append(C.string_at(name).decode()))
    ui.click(button_of(page.view(), 'system_keylock'))
    assert opened == ['systemset/keylock_page']


# Power's Boot to row: /mnt/data/boot-target, which the boot script reads with $(cat ...).
BOOT = '/mnt/data/boot-target'


def rockbox(fw, on):
    """The card's /mnt/mmc/.rockbox/rockbox: a regular file, or none."""
    def stat(version, path, info, *_):
        struct.pack_into('<I', (C.c_char * 144).from_address(info), 20, 0o100755)
        return 0 if on['card'] else -1
    fw.mock('fw___xstat', stat)


class Power:
    """Settings' Power page: stock's rows, then ringnav_powermanager's, Boot to last."""
    def __init__(self, fw, ui, files, card=True):
        self.card = {'card': card}
        rockbox(fw, self.card)
        self.files = Files(fw, files)
        Config(fw)
        self.prompts = Prompts(fw)
        self.win = ui.window('powermanager_page')
        self.view = ui.add('scroll_view', self.win, name='scroll_view_powermanager')
        fw.fn('ringnav_powermanager', C.c_int, C.c_void_p, C.c_void_p)(self.win.addr, None)

    def boot(self):
        labels = [i.labels()[0] for i in self.view.children if i.labels()[0].text.startswith('Boot to: ')]
        return labels[0] if labels else None


@pytest.mark.parametrize('saved, shown', [
    (None, 'Rockbox'),  # no choice saved: the boot script's own default
    (b'q2pod', 'Q2 Pod'), (b'q2pod\n', 'Q2 Pod'), (b'q2pod' + b'\n' * 40, 'Q2 Pod'),  # $(cat) drops trailing newlines
    (b'rockbox', 'Rockbox'), (b'q2po', 'Rockbox'), (b'q2podx', 'Rockbox'), (b'q2pod\nx', 'Rockbox'), (b'\nq2pod', 'Rockbox'),
])
def test_boot_to_reads_what_the_boot_script_would(fw, saved, shown):
    ui = Ui(fw)
    page = Power(fw, ui, {} if saved is None else {BOOT: saved})
    assert page.boot().text == f'Boot to: {shown}'


def test_boot_to_a_file_that_fails_to_read_is_rockbox(fw):
    ui = Ui(fw)
    fw.mock('fw_ferror', lambda *_: 1)
    assert Power(fw, ui, {BOOT: b'q2pod'}).boot().text == 'Boot to: Rockbox'


def test_no_rockbox_no_boot_row(fw):
    ui = Ui(fw)
    assert Power(fw, ui, {BOOT: b'q2pod'}, card=False).boot() is None


def test_boot_to_flips_and_saves_through_a_temporary_file(fw):
    ui = Ui(fw)
    page = Power(fw, ui, {})
    label = page.boot()
    ui.click(label.parent)
    assert label.text == 'Boot to: Q2 Pod' and page.files.files == {BOOT: b'q2pod'}
    ui.click(label.parent)
    assert label.text == 'Boot to: Rockbox' and page.files.files == {BOOT: b'rockbox'}
    assert {C.string_at(a[0]).decode() for _, a in fw.calls('fw_fopen')} >= {BOOT + '.tmp'}


def test_boot_to_not_saved_says_so_and_stays(fw):
    ui = Ui(fw)
    page = Power(fw, ui, {BOOT: b'rockbox'})
    page.files.fail_rename = True
    label = page.boot()
    ui.click(label.parent)
    assert label.text == 'Boot to: Rockbox' and page.files.files[BOOT] == b'rockbox'
    assert [p[3] for p in page.prompts.seen] == ['Could not save boot choice']
    assert [C.string_at(a[0]).decode() for _, a in fw.calls('fw_unlink')] == [BOOT + '.tmp']


def test_boot_to_after_rockbox_left_the_card(fw):
    ui = Ui(fw)
    page = Power(fw, ui, {})
    page.card['card'] = False
    label = page.boot()
    ui.click(label.parent)
    assert label.text == 'Boot to: Rockbox' and page.files.files == {}
    assert [p[3] for p in page.prompts.seen] == ['Rockbox is not on the card']


# Now Playing's info page.
NAV = NAVIGATION_SOURCE
def nav_define(name):
    return int(host.tools_build.need(re.search(rf'#define {name} (\d+)', NAV), f'No {name}')[1])


MARQUEE_PX, MARQUEE_MS = nav_define('MARQUEE_PX'), nav_define('MARQUEE_MS')
HOLD_MS = O['MARQUEE_HOLD_MS']
INFO = ['label_album', 'label_genre', 'label_size', 'label_year', 'label_encode', 'label_bits', 'label_kbps', 'label_sample',
        'label_location']


class Info:
    """Now Playing's info page: a label per stock line, stock's np_info having written them; rows adds
    Classic's <name>_value labels."""
    def __init__(self, fw, ui, texts, rows=False, location=b'Location'):
        self.fw, self.ui = fw, ui
        self.win = ui.window('playing_page')
        self.view = ui.add('view', self.win, (0, 30, 375, 290), 'view_info')
        self.labels = {}
        for i, name in enumerate(INFO):
            self.labels[name] = ui.add('label', self.view, (10, 20 * i, 355, 20), name)
            if rows:
                ui.add('label', self.view, (180, 20 * i, 185, 20), name + '_value')
        for name, value in texts.items():
            self.labels[name].text = value
        self.timers = []
        fw.mock('np_info', lambda *_: 7)
        fw.mock('getFormatString', lambda key, out, n, *_: C.memmove(out, location + b'\0', len(location) + 1))
        fw.mock('tk_str_start_with', lambda s, p, *_: C.string_at(s).startswith(C.string_at(p)))
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, ms)) or len(self.timers))
        self.fill = fw.fn('ringnav_np_info', C.c_int, C.c_void_p)

    def __call__(self):
        assert self.fill(self.win.addr) == 7  # stock's answer, passed on
        return {name: label.text for name, label in self.labels.items()}

    def genre(self):
        return self.win.find('ringnav_genre_scroll')


STOCK_INFO = {'label_album': 'Album:Kind of Blue', 'label_size': 'Size:35.92MB', 'label_year': 'Year:Unknown Artist',
              'label_encode': 'Format:FLAC', 'label_bits': 'Bits:16bit', 'label_kbps': 'Bitrate:950kbps',
              'label_sample': 'Sample rate:44.1kHz', 'label_location': '/mnt/mmc/Music/Miles'}


def test_info_lines_get_a_space_and_their_units(fw):
    ui = Ui(fw)
    info = Info(fw, ui, STOCK_INFO)
    assert info() == {'label_album': 'Album: Kind of Blue', 'label_genre': '', 'label_size': 'Size: 35.92 MB',
                      'label_year': 'Year: Unknown', 'label_encode': 'Format: FLAC', 'label_bits': 'Bits: 16-bit',
                      'label_kbps': 'Bitrate: 950 kbps', 'label_sample': 'Sample rate: 44.1 kHz',
                      'label_location': 'Location: /mnt/mmc/Music/Miles'}


def test_a_second_fill_rewrites_nothing(fw):
    ui = Ui(fw)
    info = Info(fw, ui, dict(STOCK_INFO, label_genre='Genre:Jazz'))
    first = info()
    set_before = len(fw.calls('widget_set_text_utf8'))
    assert info() == first and len(fw.calls('widget_set_text_utf8')) == set_before


@pytest.mark.parametrize('stock, shown', [
    ('Bitrate:VBRkbps', 'Bitrate: VBRkbps'),  # a unit after no number stays as it is
    ('Bitrate:kbps', 'Bitrate: kbps'), ('Bitrate:  950kbps', 'Bitrate: 950 kbps'), ('Bitrate', 'Bitrate'),  # no colon: left
])
def test_info_units_only_after_a_number(fw, stock, shown):
    ui = Ui(fw)
    assert Info(fw, ui, {'label_kbps': stock})()['label_kbps'] == shown


@pytest.mark.parametrize('stock, location, shown', [
    ('Location:/mnt/mmc/A', b'Location', 'Location: /mnt/mmc/A'),  # already named
    ('/mnt/mmc/A', b'Ort', 'Ort: /mnt/mmc/A'),  # the language's word
    ('/mnt/mmc/A', b'(player_location)', 'Location: /mnt/mmc/A'),  # a missing string reads "(key)"
    ('/mnt/mmc/A', b'', 'Location: /mnt/mmc/A'),
])
def test_the_folder_gets_its_location_label(fw, stock, location, shown):
    ui = Ui(fw)
    assert Info(fw, ui, {'label_location': stock}, location=location)()['label_location'] == shown


def test_classic_rows_split_name_and_value_in_its_grey(fw, st):
    ui = Ui(fw)
    Config(fw, THEME=b'1')
    info = Info(fw, ui, dict(STOCK_INFO, label_genre='Genre:Jazz'), rows=True)
    texts = info()
    value = {name: info.win.find(name + '_value') for name in INFO}
    assert texts == {'label_album': 'Album', 'label_genre': 'Genre', 'label_size': 'Size', 'label_year': 'Year',
                     'label_encode': 'Format', 'label_bits': 'Bits', 'label_kbps': 'Bitrate', 'label_sample': 'Sample rate',
                     'label_location': 'Location'}
    assert [value[n].text for n in INFO] == ['Kind of Blue', 'Jazz', '35.92 MB', 'Unknown', 'FLAC', '16-bit', '950 kbps',
                                             '44.1 kHz', '/mnt/mmc/Music/Miles']
    assert info.genre() is None  # the genre is a row like the others: no marquee
    grey = next(t for t in THEMES if t['index'] == CLASSIC)['np_info_value']  # dark Light Mode: the dark grey as it is
    ink = C.c_int(0xff000000 | (grey & 255) << 16 | grey & 0xff00 | grey >> 16).value
    assert {value[n].props['_ink'] for n in INFO} == {ink} == {value[n].props['style:normal:text_color'] for n in INFO}
    set_before = len(fw.calls('widget_set_prop_int'))
    info()
    assert len([a for _, a in fw.calls('widget_set_prop_int')[set_before:] if C.string_at(a[1]) == b'_ink']) == 0


def test_classic_rows_take_a_value_without_a_value_label(fw):
    ui = Ui(fw)
    Config(fw, THEME=b'1')
    info = Info(fw, ui, {'label_album': 'Album:Kind of Blue'}, rows=True)
    info.win.find('label_size_value').name = 'gone'
    info.labels['label_size'].text = 'Size:35.92MB'
    assert info()['label_size'] == 'Size' and info.win.find('label_album_value').text == 'Kind of Blue'


class Np:
    """Now Playing open on its info page and lit."""
    def __init__(self, fw, ui, st, info):
        st('np_win', C.c_void_p).value = info.win.addr
        self.slide = ui.add('view', info.win, name='slide_view')
        self.slide.props['value'] = 2  # view_info
        st('np_slide', C.c_void_p).value = self.slide.addr
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        self.offsets = []
        fw.mock('hscroll_label_set_xoffset', lambda w, x, *_: self.offsets.append(x))


def measured(label, width):
    """Stock's first paint: the label's text width."""
    struct.pack_into('<i', label.block, O['HSCROLL_TEXT_W'], width)


def test_the_genre_shows_in_its_own_scrolling_label(fw, st):
    ui = Ui(fw)
    info = Info(fw, ui, {'label_genre': 'Genre:Jazz'})
    info()
    genre, stock = info.genre(), info.labels['label_genre']
    assert genre.text == 'Genre: Jazz' and genre.parent is stock.parent and genre.box == stock.box and not stock.visible
    assert fw.calls('hscroll_label_set_only_focus')[0][1][:2] == (genre.addr, 1)  # no stock timer: genre_step moves it


def run(fn):
    return C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(None)


def test_a_long_genre_scrolls_to_its_end_holds_and_starts_over(fw, st):
    ui = Ui(fw)
    info = Info(fw, ui, {'label_genre': 'Genre:Progressive Electronic Dance'})
    np = Np(fw, ui, st, info)
    info()
    assert info.timers == []  # not measured yet: stock measures on the first paint
    over = 44
    measured(info.genre(), info.genre().box[2] - 4 + over)
    info()
    assert [ms for _, ms in info.timers] == [HOLD_MS]  # the new text holds at its start
    steps = []
    for _ in range(over // MARQUEE_PX + 2):
        np.offsets.clear()
        run(info.timers[-1][0])
        steps.append((np.offsets[-1], info.timers[-1][1]))
    moving = [(x, MARQUEE_MS) for x in range(MARQUEE_PX, over, MARQUEE_PX)]
    assert steps == moving + [(over, HOLD_MS), (0, HOLD_MS), (MARQUEE_PX, MARQUEE_MS)]


def test_the_genre_stops_once_dark_or_off_its_page(fw, st):
    ui = Ui(fw)
    info = Info(fw, ui, {'label_genre': 'Genre:Progressive Electronic Dance'})
    np = Np(fw, ui, st, info)
    info()
    measured(info.genre(), 600)
    info()
    np.slide.props['value'] = 1  # the lyrics
    n = len(info.timers)
    run(info.timers[-1][0])
    assert len(info.timers) == n and st('genre_timer').value == 0
    np.slide.props['value'] = 2
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    info()  # np_info's next fill restarts it, lit only
    assert len(info.timers) == n


def test_the_genre_stops_under_another_window_and_starts_again_in_front(fw, st):
    ui = Ui(fw)
    info = Info(fw, ui, {'label_genre': 'Genre:Progressive Electronic Dance'})
    Np(fw, ui, st, info)
    info()
    measured(info.genre(), 600)
    info()
    n = len(info.timers)
    over = ui.window('playmore_page')  # More, opened over Now Playing
    run(info.timers[-1][0])
    assert len(info.timers) == n and st('genre_timer').value == 0
    info()  # a fill while still covered starts nothing
    assert len(info.timers) == n
    ui.wm.children.remove(over)
    info()  # in front again: the next fill restarts it, holding at its start
    assert len(info.timers) == n + 1 and info.timers[-1][1] == HOLD_MS


def test_a_new_genre_starts_again_from_its_start(fw, st):
    ui = Ui(fw)
    info = Info(fw, ui, {'label_genre': 'Genre:Progressive Electronic Dance'})
    np = Np(fw, ui, st, info)
    info()
    measured(info.genre(), 600)
    info()
    run(info.timers[-1][0])
    run(info.timers[-1][0])
    assert st('genre_off').value == 2 * MARQUEE_PX
    removed = len(fw.calls('timer_remove'))
    info.labels['label_genre'].text = 'Genre:Ambient Dub Techno Extended'
    np.offsets.clear()
    info()
    assert info.genre().text == 'Genre: Ambient Dub Techno Extended' and np.offsets == [0] and st('genre_off').value == 0
    assert len(fw.calls('timer_remove')) == removed + 1 and info.timers[-1][1] == HOLD_MS


def test_another_windows_genre_takes_no_marquee(fw, st):
    ui = Ui(fw)
    info = Info(fw, ui, {'label_genre': 'Genre:Progressive Electronic Dance'})
    Np(fw, ui, st, info)
    st('np_win', C.c_void_p).value = None
    info()
    measured(info.genre(), 600)
    info()
    assert info.timers == [] and not st('genre_label', C.c_void_p).value


def test_no_genre_label_no_marquee(fw, st):
    ui = Ui(fw)
    info = Info(fw, ui, {'label_genre': 'Genre:Jazz'})
    Np(fw, ui, st, info)
    fw.mock('hscroll_label_create', lambda *_: 0)
    assert info()['label_genre'] == 'Genre:Jazz' and info.genre() is None


# Touch and click dispatch.
POINTER_DOWN, POINTER_MOVE, POINTER_UP = O['EVT_POINTER_DOWN'] - 1, O['EVT_POINTER_MOVE_BEFORE'] - 1, O['EVT_POINTER_UP_BEFORE'] - 1
KEY_DOWN, KEY_DOWN_BEFORE = O['EVT_KEY_DOWN'], O['EVT_KEY_DOWN_BEFORE']
SLIDE_PX = nav_define('SLIDE_PX')


class Quick(C.Structure):
    """navigation.c's quick: Quick Settings' press, hold and slider state."""
    _fields_ = [('win', C.c_void_p), ('icon', C.c_void_p), ('slider', C.c_void_p), ('pressed', C.c_void_p), ('timer', C.c_uint),
                ('x', C.c_int), ('y', C.c_int), ('sx', C.c_int), ('sy', C.c_int), ('slide', C.c_int)]


class Ple(C.Structure):
    """navigation.c's ple: the playlist editor's page and keys."""
    _fields_ = [('page', C.c_void_p), ('view', C.c_void_p), ('songs', C.c_void_p), ('picker', C.c_void_p), ('edit', C.c_void_p),
                ('match', C.c_void_p), ('dest', C.c_int), ('mode', C.c_int), ('selected', C.c_int), ('pending', C.c_int),
                ('nmatch', C.c_int), ('key', C.c_uint), ('timer', C.c_uint), ('name', C.c_char * 128), ('query', C.c_char * 128)]


class Stock:
    """Stock's dispatch and touch handlers behind ours: each event they get, as (target, type, x, y), and their answer."""
    def __init__(self, fw, ui, answer=0):
        self.fw, self.ui, self.answer, self.seen, self.touched = fw, ui, answer, [], []
        fw.mock('stock_dispatch_trampoline', lambda target, e, *_: self.got(self.seen, target, e))
        fw.mock('stock_touch_trampoline', lambda ctx, e, *_: self.got(self.touched, ctx, e))
        fw.mock('pointer_event_init', self.init)
        fw.mock('timer_remove', lambda *_: 0)
        self.dispatch = fw.fn('ringnav_dispatch', C.c_int, C.c_void_p, C.c_void_p)
        self.touch = fw.fn('ringnav_touch', C.c_int, C.c_void_p, C.c_void_p)

    def got(self, log, target, e):
        log.append((target, *self.read(e)))
        return self.answer

    @staticmethod
    def read(e):
        return (C.c_int.from_address(e).value, *struct.unpack('<ii', C.string_at(e + EVENT_X, 8))) if e else (None, None, None)

    @staticmethod
    def init(ev, kind, target, x, y, *_):
        C.memset(ev, 0, 0x30)
        struct.pack_into('<i', (C.c_char * 0x30).from_address(ev), 0, kind)
        struct.pack_into('<ii', (C.c_char * 0x30).from_address(ev), EVENT_X, x, y)
        return ev

    def send(self, target, kind, key=0, x=None, y=None):
        e = event(kind, key, x, y)
        return self.dispatch(target.addr if target else None, e)


def test_a_click_reaches_stock_and_ends_whatever_was_pending(fw, st):
    ui = Ui(fw)
    page = ui.window('localmusic_page')
    row = ui.add('button', page)
    stock = Stock(fw, ui, answer=5)
    st('center_timer').value, st('fx_timer').value, st('touch_mode').value = 7, 9, 0
    assert stock.send(row, EVT_CLICK) == 5
    assert stock.seen == [(row.addr, EVT_CLICK, 0, 0)]
    # the outline hides (a tap is touch), and the pending confirmation and bump go
    assert st('touch_mode').value == 1 and st('center_timer').value == 0 and st('fx_timer').value == 0


def test_a_click_a_pull_claimed_never_reaches_stock(fw):
    ui = Ui(fw)
    page = ui.window('localmusic_page')
    view = ui.add('scroll_view', page, _pull_suppress=1)
    row = ui.add('button', ui.add('list_item', view))
    stock = Stock(fw, ui)
    assert stock.send(row, EVT_CLICK) == STOP and stock.seen == []


def test_the_release_of_a_quick_settings_hold_is_no_click(fw):
    ui = Ui(fw)
    icon = ui.add('image', ui.window('statusbar_dialog'), _quick_hold=-2)
    stock = Stock(fw, ui)
    assert stock.send(icon, EVT_CLICK) == STOP and stock.seen == []


@pytest.mark.parametrize('button, clock, back', [('btn_enter', 0, 1), ('btn_enter', 3, 1), ('btn_cancel', 0, 0)])
def test_set_time_ok_goes_back_to_the_time_page(fw, st, button, clock, back):
    ui = Ui(fw)
    page = ui.window('manualtime_page')
    target = ui.add('button', page, name=button)
    stock = Stock(fw, ui)
    st('clock_timer').value = clock
    stock.send(target, EVT_CLICK)
    assert len(stock.seen) == 1 and len(fw.calls('navigator_back')) == back
    # a running bar clock shows the time just set at once: it reads the clock again
    assert bool(fw.calls('fw_time')) == bool(clock)


def test_set_time_ok_that_closed_its_page_goes_nowhere_else(fw):
    ui = Ui(fw)
    page = ui.window('manualtime_page')
    target = ui.add('button', page, name='btn_enter')
    stock = Stock(fw, ui)
    fw.mock('stock_dispatch_trampoline', lambda *_: ui.wm.children.remove(page) or 0)
    stock.send(target, EVT_CLICK)
    assert not fw.calls('navigator_back')


@pytest.mark.parametrize('kind', [KEY_DOWN, KEY_DOWN_BEFORE, O['EVT_KEY_UP'], O['EVT_KEY_LONG']])
def test_the_playlist_editors_buttons_never_reach_stock(fw, kind):
    ui = Ui(fw)
    target = ui.add('button', ui.window('home_page'))
    stock = Stock(fw, ui)
    fw.var('playlists__ple', Ple).page = ui.window('playlist_editor').addr
    assert stock.send(target, kind, O['KEY_BACK_BTN']) == STOP and stock.seen == []
    assert stock.send(target, kind, O['KEY_RETURN']) == 0 and len(stock.seen) == 1
    # the window manager's own still reaches stock: its key hooks there (ringnav_keydown, ringnav) move the editor
    assert stock.send(ui.wm, kind, O['KEY_BACK_BTN']) == 0 and stock.seen[-1][0] == ui.wm.addr


@pytest.mark.parametrize('top, inside, passes', [
    ('coverflow_page', True, True), ('coverflow_page', False, False), ('artistinfo_page', False, False),
    ('localmusic_page', False, True)])
def test_wheel_keys_to_a_covered_windows_widget_are_dropped(fw, top, inside, passes):
    ui = Ui(fw)
    under = ui.window('localmusic_page')
    covered = ui.add('list_view', under)
    page = ui.window(top)
    target = ui.add('view', page) if inside else covered
    stock = Stock(fw, ui)
    for key in (O['KEY_PREV'], O['KEY_NEXT']):
        assert stock.send(target, KEY_DOWN, key) == (0 if passes else STOP)
    assert len(stock.seen) == (2 if passes else 0)
    stock.send(target, KEY_DOWN, O['KEY_CENTER'])
    assert len(stock.seen) == (3 if passes else 1)


def test_a_key_ends_a_pending_pull(fw, st):
    ui = Ui(fw)
    page = ui.window('localmusic_page')
    prompt = ui.add('label', page, name='_pull_prompt')
    stock = Stock(fw, ui)
    st('pull_page', C.c_void_p).value = page.addr
    stock.send(page, KEY_DOWN_BEFORE, O['KEY_CENTER'])
    assert not st('pull_page', C.c_void_p).value and not prompt.visible


class QuickPage:
    """Quick Settings pulled down (state 3): its dialog with the brightness slider."""
    def __init__(self, fw, ui, st, state=3):
        self.dialog = ui.window('statusbar_dialog')
        self.slider = ui.add('slider', ui.add('view', self.dialog), (40, 200, 300, 30), 'slider_backlight')
        self.state = C.c_int.from_address(C.addressof(fw.var('fw_ctx_g_dropdown_menu_state', C.c_char)))
        self.state.value = state
        self.quick = fw.var('navigation__quick', Quick)


def test_the_brightness_slider_takes_a_sideways_drag(fw, st):
    ui = Ui(fw)
    qs = QuickPage(fw, ui, st)
    stock = Stock(fw, ui)
    s = qs.slider
    assert stock.send(s, POINTER_DOWN, x=100, y=210) == 0 and stock.seen == []  # held back
    stock.send(s, POINTER_MOVE, x=100 + SLIDE_PX - 1, y=210)
    assert stock.seen == []
    stock.send(s, POINTER_MOVE, x=100 + SLIDE_PX, y=212)
    # the press it never had, where it began, then this move
    assert stock.seen == [(s.addr, POINTER_DOWN, 100, 210), (s.addr, POINTER_MOVE, 100 + SLIDE_PX, 212)]
    stock.send(s, POINTER_UP, x=150, y=212)
    assert stock.seen[-1] == (s.addr, POINTER_UP, 150, 212) and not qs.quick.slider


def test_the_brightness_slider_gives_a_vertical_drag_to_the_swipe(fw, st):
    ui = Ui(fw)
    qs = QuickPage(fw, ui, st)
    stock = Stock(fw, ui)
    s = qs.slider
    stock.send(s, POINTER_DOWN, x=100, y=210)
    stock.send(s, POINTER_MOVE, x=102, y=210 - SLIDE_PX)
    stock.send(s, POINTER_MOVE, x=100 + 3 * SLIDE_PX, y=190)  # sideways once the swipe has it: still the swipe's
    stock.send(s, POINTER_UP, x=130, y=150)
    assert stock.seen == [] and qs.quick.slide == -1 and not qs.quick.slider


def test_a_tap_on_the_brightness_slider_is_its_press_and_release(fw, st):
    ui = Ui(fw)
    qs = QuickPage(fw, ui, st)
    stock = Stock(fw, ui)
    s = qs.slider
    stock.send(s, POINTER_DOWN, x=100, y=210)
    stock.send(s, POINTER_UP, x=101, y=211)
    assert stock.seen == [(s.addr, POINTER_DOWN, 100, 210), (s.addr, POINTER_UP, 101, 211)]


def test_the_slider_loses_its_drag_while_the_pull_down_slides(fw, st):
    ui = Ui(fw)
    qs = QuickPage(fw, ui, st)
    stock = Stock(fw, ui)
    s = qs.slider
    stock.send(s, POINTER_DOWN, x=100, y=210)
    stock.send(s, POINTER_MOVE, x=130, y=210)
    qs.state.value = 2
    stock.send(s, POINTER_MOVE, x=140, y=210)
    assert [k for _, k, _, _ in stock.seen] == [POINTER_DOWN, POINTER_MOVE, O['EVT_POINTER_ABORT']]
    stock.send(s, POINTER_MOVE, x=150, y=210)
    assert len(stock.seen) == 3


def test_settings_brightness_slider_is_stocks_alone(fw, st):
    ui = Ui(fw)
    slider = ui.add('slider', ui.window('backlight_page'), name='slider_backlight')
    stock = Stock(fw, ui)
    stock.send(slider, POINTER_DOWN, x=100, y=210)
    assert stock.seen == [(slider.addr, POINTER_DOWN, 100, 210)] and not fw.var('navigation__quick', Quick).slider


def pressdown(fw):
    return C.c_int.from_address(C.addressof(fw.var('fw_ctx_g_pressdown_y', C.c_char)))


@pytest.mark.parametrize('theme, start, moved, counts', [
    (b'1', O['QS_SWIPE_Y'], SLIDE_PX, 1), (b'1', O['QS_SWIPE_STOCK'] - 1, SLIDE_PX, 1),
    (b'1', O['QS_SWIPE_Y'] - 1, SLIDE_PX, 0), (b'1', O['QS_SWIPE_Y'], SLIDE_PX - 1, 0), (b'0', O['QS_SWIPE_Y'], SLIDE_PX, 0)])
def test_classics_swipe_up_starts_above_stocks_line(fw, st, theme, start, moved, counts):
    ui = Ui(fw)
    Config(fw, THEME=theme)
    qs = QuickPage(fw, ui, st)
    stock = Stock(fw, ui)
    pressdown(fw).value = start
    stock.send(qs.dialog, POINTER_MOVE, x=100, y=start - moved)
    # counted, stock's handler sees the touch as one from its own line
    assert pressdown(fw).value == (O['QS_SWIPE_STOCK'] if counts else start)
    assert len(stock.seen) == 1


def test_a_swipe_the_slider_owns_is_not_counted(fw, st):
    ui = Ui(fw)
    Config(fw, THEME=b'1')
    qs = QuickPage(fw, ui, st)
    stock = Stock(fw, ui)
    qs.quick.slider, qs.quick.slide = qs.slider.addr, 1
    pressdown(fw).value = O['QS_SWIPE_Y']
    stock.send(qs.dialog, POINTER_MOVE, x=100, y=O['QS_SWIPE_Y'] - 2 * SLIDE_PX)
    assert pressdown(fw).value == O['QS_SWIPE_Y']


def test_a_swipe_is_counted_on_the_dialogs_own_move_only(fw, st):
    ui = Ui(fw)
    Config(fw, THEME=b'%d' % CLASSIC)
    qs = QuickPage(fw, ui, st)
    stock = Stock(fw, ui)
    pressdown(fw).value = O['QS_SWIPE_Y']
    inner = qs.slider.parent  # the view the slider sits in, under the dialog
    stock.send(inner, POINTER_MOVE, x=100, y=O['QS_SWIPE_Y'] - SLIDE_PX)
    assert pressdown(fw).value == O['QS_SWIPE_Y']  # a widget inside it: left alone, the dialog's own move counts
    stock.send(qs.dialog, POINTER_MOVE, x=100, y=O['QS_SWIPE_Y'] - SLIDE_PX)
    assert pressdown(fw).value == O['QS_SWIPE_STOCK']


@pytest.mark.parametrize('kind, dx, cancels', [
    (POINTER_MOVE, 10, 0), (POINTER_MOVE, 11, 1), (POINTER_UP, 0, 1), (O['EVT_POINTER_ABORT'], 0, 1), (EVT_CLICK, 0, 1),
    (KEY_DOWN_BEFORE, None, 1)])
def test_a_quick_settings_hold_ends_with_a_move_or_any_end(fw, st, kind, dx, cancels):
    ui = Ui(fw)
    qs = QuickPage(fw, ui, st)
    stock = Stock(fw, ui)
    icon = ui.add('image', qs.dialog)
    qs.quick.timer, qs.quick.x, qs.quick.y, qs.quick.win, qs.quick.icon = 4, 60, 60, qs.dialog.addr, icon.addr
    if dx is None:
        stock.send(icon, kind, O['KEY_CENTER'])
    else:
        stock.send(icon, kind, x=60 + dx, y=60)
    assert (qs.quick.timer, qs.quick.icon) == ((0, None) if cancels else (4, icon.addr))


def test_a_release_lets_go_of_the_pressed_quick_button(fw, st):
    ui = Ui(fw)
    qs = QuickPage(fw, ui, st)
    stock = Stock(fw, ui)
    icon = ui.add('image', qs.dialog)
    qs.quick.pressed = icon.addr
    stock.send(icon, POINTER_MOVE, x=1, y=1)
    assert qs.quick.pressed == icon.addr
    stock.send(icon, POINTER_UP, x=1, y=1)
    assert not qs.quick.pressed and [a[0] for _, a in fw.calls('widget_invalidate_force')] == [icon.addr]


def test_a_touch_reaches_stock_and_hides_the_outline_once(fw, st):
    ui = Ui(fw)
    page = ui.window('localmusic_page')
    stock = Stock(fw, ui, answer=3)
    st('center_timer').value, st('fx_timer').value = 7, 9
    down = event(O['EVT_POINTER_DOWN'], x=100, y=100)
    assert stock.touch(None, down) == 3 and stock.touched == [(0, O['EVT_POINTER_DOWN'], 100, 100)]
    assert st('touch_mode').value == 1 and st('center_timer').value == 0 and st('fx_timer').value == 0
    repainted = [a[0] for _, a in fw.calls('widget_invalidate_force')]
    assert repainted == [page.addr]
    stock.touch(None, event(O['EVT_POINTER_MOVE_BEFORE'], x=100, y=120))
    assert [a[0] for _, a in fw.calls('widget_invalidate_force')] == repainted  # already hidden


@pytest.mark.parametrize('claimed', [0, 1])
def test_a_touch_on_a_list_marks_it_touched_unless_stock_took_it(fw, st, claimed):
    ui = Ui(fw)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    fw.mock('widget_get_prop_bool', lambda w, name, default, *_: default)
    page = ui.window('localmusic_page')
    view = ui.add('scroll_view', page, (0, 30, 375, 290), 'scroll_view_localmusic')
    view.block[O['VIEW_VERTICAL']] = b'\1'  # yslidable: a list the wheel moves
    for _ in range(3):
        ui.add('button', ui.add('list_item', view, (0, 0, 375, 70)))
    stock = Stock(fw, ui, answer=claimed)
    stock.touch(None, event(O['EVT_POINTER_DOWN'], x=100, y=100))
    # the list draws its touch look, the same touch going on to stock's tap and drag
    assert view.props.get('_ringnav_touch', 0) == (0 if claimed else 1)
    assert (view.addr in [a[0] for _, a in fw.calls('widget_invalidate_force')]) == (not claimed)


def test_a_touch_cancels_a_pending_untouch(fw, st):
    ui = Ui(fw)
    page = ui.window('localmusic_page')
    stock = Stock(fw, ui)
    st('touch_mode').value, st('untouch_timer').value = 1, 6
    stock.touch(None, event(O['EVT_POINTER_DOWN'], x=1, y=100))
    assert st('untouch_timer').value == 0 and st('touch_mode').value == 1
    assert [a[0] for _, a in fw.calls('timer_remove')][:1] == [6] and fw.calls('widget_invalidate_force')[0][1][0] == page.addr


def test_untouch_brings_the_outline_back(fw, st):
    ui = Ui(fw)
    page = ui.window('localmusic_page')
    st('touch_mode').value, st('untouch_timer').value = 1, 6
    assert fw.fn('navigation__untouch', C.c_int, C.c_void_p)(None) == 0
    assert (st('touch_mode').value, st('untouch_timer').value) == (0, 0)
    assert [a[0] for _, a in fw.calls('widget_invalidate_force')] == [page.addr]


@pytest.mark.parametrize('name, at, blurs', [('search_edit', (40, 10), 1), ('search_edit', (75, 10), 0), ('search_edit', (40, 35), 0),
                                             ('other_edit', (40, 10), 0)])
def test_t9s_chevron_goes_back_before_the_box_blurs(fw, st, name, at, blurs):
    ui = Ui(fw)
    page = ui.window('search_page')
    back = ui.add('image', page, (30, 0, 40, 30))
    edit = ui.add('edit', page, (72, 2, 244, 27), name)
    st('t9_back', C.c_void_p).value = back.addr
    im = C.create_string_buffer(0x20)
    C.c_void_p.from_address(C.addressof(im) + O['IM_WIDGET']).value = edit.addr
    fw.mock('input_method', lambda *_: C.addressof(im))
    stock = Stock(fw, ui)
    stock.touch(None, event(O['EVT_POINTER_DOWN'], x=at[0], y=at[1]))
    assert [a[:2] for _, a in fw.calls('widget_set_focused')] == ([(edit.addr, 0)] if blurs else [])
    assert len(fw.calls('input_method_request')) == blurs and len(stock.touched) == 1


class Scrub:
    """Now Playing after a paused seek: the settle wait running, its slider at (20, 200, 335, 30)."""
    def __init__(self, fw, ui, st, status):
        page = ui.window('playing_page')
        self.slider = ui.add('slider', page, (20, 200, 335, 30))
        st('np_slider', C.c_void_p).value = self.slider.addr
        st('settle_timer').value, st('np_press').value = 8, 5
        fw.mock('mclGetPlayStatus', lambda *_: status)


@pytest.mark.parametrize('status, at, keeps', [(3, (100, 100), 1), (3, (100, 210), 0), (1, (100, 100), 0)])
def test_a_swipe_keeps_a_paused_seeks_preview(fw, st, status, at, keeps):
    ui = Ui(fw)
    Scrub(fw, ui, st, status)
    stock = Stock(fw, ui)
    stock.touch(None, event(O['EVT_POINTER_DOWN'], x=at[0], y=at[1]))
    # off the slider while paused the seek waits on; on it, or playing, the wait ends as any scrub's
    assert st('np_press').value == 0 and st('settle_timer').value == (8 if keeps else 0)


# The rows' layouts: stock's default row layouter with ours in its slot, and the settings lists'.
VTABLE, LIST_LAYOUT = O['DEFAULT_LAYOUT_VTABLE'], O['LIST_VIEW_LAYOUT']
MARGIN, SPACING = 8, 6


@pytest.fixture
def stand_in():
    """place(address, bytes): stock's memory at a fixed address, its page made executable for stand-in code; put
    back after the test, as conftest's map outlives a load."""
    saved = []
    def place(address, data):
        claim(address)
        saved.append((address, C.string_at(address, len(data))))
        C.memmove(address, data, len(data))
    yield place
    for address, data in reversed(saved):
        C.memmove(address, data, len(data))


def logger(log, result=0):
    """x86-64: its first two arguments into log[0] and log[1], then result."""
    return (b'\x48\xb8' + struct.pack('<Q', C.addressof(log)) + bytes.fromhex('488938 48897008') +
            b'\xb8' + struct.pack('<i', result) + b'\xc3')


@pytest.fixture
def default_layout(stand_in):
    """Stock's default row layouter: its vtable's third slot a stand-in logging (layout, row) and returning 4."""
    log = (C.c_uint64 * 2)()
    code = VTABLE + 0x100
    stand_in(code, logger(log, 4))
    stand_in(VTABLE, struct.pack('<8I', *(0x5f0000 + 4 * i for i in range(8))))
    stand_in(VTABLE + 8, struct.pack('<I', code))
    return log


def layout():
    lay = C.create_string_buffer(0x40)
    struct.pack_into('<B', lay, O['DEFAULT_LAYOUT_X_MARGIN'], MARGIN)
    struct.pack_into('<B', lay, O['DEFAULT_LAYOUT_SPACING'], SPACING)
    return lay


class Rows:
    """A list's rows in a window, laid out by compact_row_layout."""
    def __init__(self, fw, ui, st, geometry, window='localmusic_page', geom='GEOM_IPOD', theme=b'0'):
        names, self.rows = geometry
        self.names = names
        Config(fw, THEME=theme)
        st('settings_read').value = 0  # read again: a test may lay out under two themes
        st('geom').value = names[geom]
        fw.mock('tk_str_start_with', lambda s, p, *_: C.string_at(s).startswith(C.string_at(p)))
        self.ui, self.fw = ui, fw
        self.view = ui.add('scroll_view', ui.window(window), (0, 30, 375, 290))
        self.lay = layout()
        self.run = fw.fn('navigation__compact_row_layout', C.c_int, C.c_void_p, C.c_void_p)
        self.theme = next(t for t in THEMES if t['index'] == int(theme))

    def row(self, h, *children):
        """A row h high holding children: (kind, box, name) each, a 'view' kind with its own (kind, box, name) list."""
        item = self.ui.add('list_item', self.view, (0, 0, 375, h))
        for kind, box, name, *inner in children:
            w = self.ui.add(kind, item, box, name)
            for k, b, n in (inner[0] if inner else ()):
                self.ui.add(k, w, b, n)
        return item

    def __call__(self, item):
        return self.run(C.addressof(self.lay), item.addr)


ICON = ('image', (MARGIN, 10, 52, 52), 'img_icon')


def test_a_rows_title_takes_the_width_its_siblings_leave(fw, st, geometry, default_layout):
    ui = Ui(fw)
    rows = Rows(fw, ui, st, geometry)
    item = rows.row(72, ICON, ('hscroll_label', (66, 0, 200, 72), 'title'))
    title = item.children[1]
    assert rows(item) == 4 and list(default_layout) == [C.addressof(rows.lay), item.addr]  # then stock lays it out
    assert title.box[2] == 375 - 2 * MARGIN - (52 + SPACING)
    assert fw.calls('hscroll_label_set_only_focus')[-1][1][:2] == (title.addr, 1)  # the ellipsis, not stock's scroll
    item.children[0].visible = 0
    rows(item)
    assert title.box[2] == 375 - 2 * MARGIN  # a hidden sibling leaves its width


def test_a_drill_rows_title_ends_clear_of_the_chevron(fw, st, geometry, default_layout):
    ui = Ui(fw)
    after_icon = ('hscroll_label', (MARGIN + 52 + SPACING, 0, 200, 72), 'title')  # where stock's layout puts it
    plain = Rows(fw, ui, st, geometry)
    a = plain.row(72, ICON, after_icon)
    plain(a)
    drill = Rows(fw, ui, st, geometry, window='home_page')  # DRILL: its rows open lists, with no stock list_into
    b = drill.row(72, ICON, after_icon)
    drill(b)
    right = [r.children[1].box[0] + r.children[1].box[2] for r in (a, b)]
    assert right[0] == 375 - MARGIN  # a plain row's title runs to the margin
    # a drill row's ends a spacing short of the drawn chevron, whose left edge is CHEVRON_W from the row's right
    # edge (docs/internals.md), as a title ends before a stock img_into
    assert right[1] + SPACING == 375 - O['CHEVRON_W']


TWO_LINES = [('hscroll_label', (0, 0, 200, 16), 'title'), ('hscroll_label', (0, 16, 200, 16), 'artist')]


def test_classics_one_line_row_hides_its_art_and_second_line(fw, st, geometry, default_layout):
    ui = Ui(fw)
    rows = Rows(fw, ui, st, geometry, geom='GEOM_CLASSIC_LIGHT', theme=b'1')
    pitch = rows.rows['boot_values'][rows.names['GEOM_CLASSIC_LIGHT']][rows.names['G_PITCH']]
    text_x = rows.rows['geom_vals'][rows.names['GEOM_CLASSIC_LIGHT']][rows.names['V_TEXT_X']]
    item = rows.row(pitch, ICON, ('image', (66, 0, 20, 20), 'img_playing'), ('image', (90, 0, 26, 26), 'img_choice0'),
                    ('view', (MARGIN, 0, 200, pitch), 'view_info', TWO_LINES))
    text = item.children[3]
    title, second = text.children
    rows(item)
    assert [c.visible for c in item.children] == [0, 0, 1, 1]  # stock's choice box stays
    assert (title.visible, second.visible) == (1, 0)
    width = 375 - 2 * MARGIN - (26 + SPACING)
    # text leading the row moves to the set's inset, its right edge kept
    assert text.box[0] == text_x and text.box[0] + text.box[2] == MARGIN + width
    assert title.box[2] == width - title.box[0]


def test_classics_two_line_row_without_art_ends_its_text_at_the_themes_column(fw, st, geometry, default_layout):
    ui = Ui(fw)
    rows = Rows(fw, ui, st, geometry, geom='GEOM_CLASSIC_LIGHT', theme=b'1')
    text_x = rows.rows['geom_vals'][rows.names['GEOM_CLASSIC_LIGHT']][rows.names['V_TEXT_X']]
    item = rows.row(52, ('image', (MARGIN, 0, 0, 52), 'img_icon'), ('view', (MARGIN, 0, 300, 52), 'view_info', TWO_LINES))
    with_chevron = rows.row(52, ('image', (MARGIN, 0, 0, 52), 'img_icon'), ('view', (MARGIN, 0, 300, 52), 'view_info', TWO_LINES),
                            ('image', (300, 0, 58, 52), 'img_into'))
    with_chevron.children[2].props['image'] = b'list_into'
    rows(item)
    rows(with_chevron)
    text = item.children[1]
    title, second = text.children
    assert not item.children[0].visible and second.visible  # the two lines stay
    # the text's box on screen, hscroll_label's 2 px margin past the theme's column
    assert (text.box[0], text.box[0] + text.box[2]) == (text_x, rows.theme['row_text_end'] + 2)
    assert title.box[2] == second.box[2] == text.box[2]
    # a chevron's row ends its text where an art row's does
    other = with_chevron.children[1]
    assert other.box[0] + other.box[2] == rows.theme['art_text_end'] + 2


def test_classics_art_row_puts_cover_and_text_on_the_themes_columns(fw, st, geometry, default_layout):
    ui = Ui(fw)
    rows = Rows(fw, ui, st, geometry, geom='GEOM_CLASSIC_LIGHT', theme=b'1')
    item = rows.row(52, ('image', (MARGIN, 0, 52, 52), 'img_icon'), ('view', (66, 0, 300, 52), 'view_info',
                    [('hscroll_label', (0, 0, 200, 26), 'title'), ('view', (0, 26, 151, 26), 'meta')]))
    meta_label = ui.add('hscroll_label', item.children[1].children[1], (0, 0, 151, 26))
    rows(item)
    art, text = item.children
    t = rows.theme
    assert art.box[0] == t['art_x'] and text.box[0] == t['art_text_x'] - 2
    assert text.box[0] + text.box[2] == t['art_text_end'] + 2
    # stock's 151 px second line takes the text's width, as the title does
    title, meta = text.children
    assert title.box[2] == meta.box[2] == meta_label.box[2] == text.box[2]
    # the second line takes the theme's muted colour, the title keeps its style's
    assert 'style:normal:text_color' in meta_label.props and 'style:normal:text_color' not in title.props


def test_an_art_row_with_stocks_choice_box_keeps_stocks_places(fw, st, geometry, default_layout):
    ui = Ui(fw)
    rows = Rows(fw, ui, st, geometry, geom='GEOM_CLASSIC_LIGHT', theme=b'1')
    item = rows.row(52, ('image', (MARGIN, 0, 26, 52), 'img_choice0'), ('image', (40, 0, 52, 52), 'img_icon'),
                    ('view', (100, 0, 200, 52), 'view_info', TWO_LINES))
    rows(item)
    assert [c.box[0] for c in item.children] == [MARGIN, 40, 100]


def test_a_rows_second_line_takes_the_meta_grey_once_where_the_theme_has_a_meta_face(fw, st, geometry, default_layout):
    for theme, grey in ((b'1', True), (b'0', False)):
        ui = Ui(fw)
        rows = Rows(fw, ui, st, geometry, theme=theme)
        item = rows.row(72, ('view', (MARGIN, 0, 300, 72), 'view_info', TWO_LINES))
        rows(item)
        second = item.children[0].children[1]
        assert ('style:normal:text_color' in second.props) == grey
        if grey:
            second.props['style:normal:text_color'] = 0
            rows(item)  # already in its face: left as it is
            assert second.props['style:normal:text_color'] == 0


def test_a_playlist_row_keeps_its_own_places(fw, st, geometry, default_layout):
    ui = Ui(fw)
    rows = Rows(fw, ui, st, geometry, geom='GEOM_CLASSIC_LIGHT', theme=b'%d' % CLASSIC)
    h = rows.theme['page_detail']  # pl_row's height: a name over its count
    def playlist(trailing, image):
        item = rows.row(h)
        item.props['_pl_row'] = 1
        button = ui.add('button', item, (0, 0, 375, h))
        mark = ui.add('image', button, (280, 10, 40, 32), trailing)  # stock's own place
        mark.props['image'] = image
        info = ui.add('view', button, (72, 0, 200, h), 'view_info')
        for y in (0, 26):
            ui.add('hscroll_label', info, (0, y, 200, 26))
        return button, mark, info
    dots, tick = playlist('img_more0', b'unselect'), playlist('img_choice0', b'mulselect')  # the list's, a picker's
    for button, _, _ in (dots, tick):
        assert rows(button) == 0
    assert list(default_layout) == [0, 0]  # stock's layout never runs
    for button, mark, info in (dots, tick):
        name, count = info.children
        assert (mark.box[1], mark.box[3]) == (0, h) and mark.box[0] + mark.box[2] <= 375  # the row's height, in it
        assert info.box[0] + info.box[2] <= mark.box[0]  # the text ends clear of it
        assert name.box[1] < count.box[1] and name.box[2] == count.box[2] == info.box[2]
    # the dots and a picker's mark share one column, centred on the tick
    assert dots[1].box[0] + dots[1].box[2] / 2 == tick[1].box[0] + tick[1].box[2] / 2
    assert tick[1].props['image'] == b'select'  # stock's disc drawn as a picker's tick
    # the selected row's chevron shows in the dots' place: the text ends clear of it too
    assert dots[2].box[0] + dots[2].box[2] <= 375 - O['CHEVRON_W']


def test_a_row_of_choice_boxes_alone_is_stocks(fw, st, geometry, default_layout):
    ui = Ui(fw)
    rows = Rows(fw, ui, st, geometry, geom='GEOM_CLASSIC_LIGHT', theme=b'1')
    item = rows.row(52, ('image', (MARGIN, 0, 26, 52), 'img_choice0'), ('image', (40, 0, 26, 52), 'img_choice1'))
    assert rows(item) == 4 and [c.visible for c in item.children] == [1, 1] and [c.box[0] for c in item.children] == [MARGIN, 40]


def test_a_row_with_no_title_is_stocks(fw, st, geometry, default_layout):
    ui = Ui(fw)
    rows = Rows(fw, ui, st, geometry, geom='GEOM_CLASSIC_LIGHT', theme=b'1')
    item = rows.row(32, ICON, ('label', (66, 0, 200, 32), 'label'))
    assert rows(item) == 4 and [c.visible for c in item.children] == [1, 1] and item.children[1].box == (66, 0, 200, 32)


def layout_of(fw, row):
    """A row with stock's children layout object at W_CHILDREN_LAYOUT."""
    lay = C.create_string_buffer(0x40)
    C.c_void_p.from_address(row.addr + O['W_CHILDREN_LAYOUT']).value = C.addressof(lay)
    return lay


def test_a_compact_rows_layout_is_stocks_with_ours_in_its_slot(fw, default_layout):
    ui = Ui(fw)
    rows = [ui.add('list_item', ui.window('localmusic_page')) for _ in range(2)]
    lays = [layout_of(fw, r) for r in rows]
    install = fw.fn('compact_set_row_layout', C.c_int, C.c_void_p, C.c_char_p)
    assert [install(r.addr, b'default(h=72)') for r in rows] == [0, 0]
    vtables = [C.c_void_p.from_buffer(lay, O['CHILDREN_LAYOUT_VTABLE']).value or 0 for lay in lays]
    assert vtables[0] == vtables[1]  # one table, made once
    entries = list((C.c_uint * 8).from_address(vtables[0]))
    ours = C.cast(fw.lib.navigation__compact_row_layout, C.c_void_p).value or 0
    assert entries == [0x5f0000 + 4 * i if i != 2 else ours & 0xffffffff for i in range(8)]
    assert [C.string_at(a[1]) for _, a in fw.calls('widget_set_children_layout')] == [b'default(h=72)'] * 2


def test_a_row_stock_refused_a_layout_keeps_its_own(fw, default_layout):
    ui = Ui(fw)
    row = ui.add('list_item', ui.window('localmusic_page'))
    lay = layout_of(fw, row)
    fw.mock('widget_set_children_layout', lambda *_: 3)
    assert fw.fn('compact_set_row_layout', C.c_int, C.c_void_p, C.c_char_p)(row.addr, b'x') == 3
    assert not C.c_void_p.from_buffer(lay, O['CHILDREN_LAYOUT_VTABLE']).value


@pytest.mark.parametrize('geom, ours', [('GEOM_IPOD', 0), ('GEOM_CLASSIC_LIGHT', 1)])
def test_stocks_table_rows_take_ours_only_under_a_set_that_repitches_them(fw, st, geometry, default_layout, geom, ours):
    names, _ = geometry
    st('geom').value = names[geom]
    ui = Ui(fw)
    row = ui.add('list_item', ui.window('localmusic_page'))
    lay = layout_of(fw, row)
    assert fw.fn('stock_set_row_layout', C.c_int, C.c_void_p, C.c_char_p)(row.addr, b'x') == 0
    assert bool(C.c_void_p.from_buffer(lay, O['CHILDREN_LAYOUT_VTABLE']).value) == bool(ours)


@pytest.mark.parametrize('geom', ['GEOM_IPOD', 'GEOM_CLASSIC_LIGHT'])
def test_queues_label_goes_to_the_sets_text_inset(fw, st, geometry, geom):
    names, rows_ = geometry
    st('geom').value = names[geom]
    Config(fw, THEME=b'1' if geom != 'GEOM_IPOD' else b'0')
    ui = Ui(fw)
    button = ui.add('button', ui.window('playlist_page'), (O['SET_STOCK_X'], 0, 335, 70))
    label = ui.add('hscroll_label', button, (10, 0, 260, 70))
    other = ui.add('hscroll_label', button, (72, 0, 200, 70))
    text = fw.fn('stock_row_text', C.c_int, C.c_void_p, C.c_char_p)
    assert text(label.addr, b'Queue') == 0 and text(other.addr, b'Song') == 0
    assert (label.text, other.text) == ('Queue', 'Song')
    if geom == 'GEOM_IPOD':
        assert label.box == (10, 0, 260, 70) and 'style:normal:font_name' not in label.props
    else:
        inset = rows_['geom_vals'][names[geom]][names['V_TEXT_X']]
        assert button.box[0] + label.box[0] == inset and label.box[0] + label.box[2] == 10 + 260  # its right edge kept
        assert other.box == (72, 0, 200, 70) and 'style:normal:font_name' in label.props


@pytest.mark.parametrize('geom', ['GEOM_IPOD', 'GEOM_CLASSIC_LIGHT'])
def test_the_playing_rows_red_keeps_its_colour_under_a_repitched_set(fw, st, geometry, geom):
    names, _ = geometry
    st('geom').value = names[geom]
    ui = Ui(fw)
    playing, other = (ui.add('hscroll_label', ui.window('playlist_page')) for _ in range(2))
    style = fw.fn('stock_row_style', C.c_int, C.c_void_p, C.c_char_p)
    playing.text, other.text = 'Song', 'Other'
    style(playing.addr, b's_scrlabel_red24l')
    style(other.addr, b's_scrlabel_white24l')
    assert (playing.props['style'], other.props['style']) == ('s_scrlabel_red24l', 's_scrlabel_white24l')
    assert (playing.text, other.text) == ('▶ Song', 'Other')
    if geom == 'GEOM_IPOD':
        assert 'style:normal:text_color' not in playing.props
        return
    red = O['STOCK_RED']
    colour = 'style:normal:text_color'
    assert playing.props[colour] == C.c_int(0xff000000 | (red & 255) << 16 | red & 0xff00 | red >> 16).value
    # every bind writes its colour: another row's is set too, not the red, and a reused row loses the red
    assert colour in other.props and other.props[colour] != playing.props[colour]
    style(playing.addr, b's_scrlabel_white24l')
    assert playing.props[colour] == other.props[colour]


@pytest.mark.parametrize('geom', ['GEOM_IPOD', 'GEOM_CLASSIC_LIGHT'])
def test_mores_speed_row_keeps_stocks_height_its_labels_before_the_buttons(fw, st, geometry, geom):
    names, rows_ = geometry
    st('geom').value = names[geom]
    Config(fw, THEME=b'1' if geom != 'GEOM_IPOD' else b'0')
    ui = Ui(fw)
    win = ui.window('playmore_page')
    row = ui.add('list_item', ui.add('scroll_view', win), (0, 0, 375, 32))
    button = ui.add('button', row, (O['SET_STOCK_X'], 0, 335, 70))
    icon = ui.add('image', button, (10, 9, O['SET_STOCK_ICON'], 52))
    speed = ui.add('hscroll_label', button, (72, 5, 120, 30))
    value = ui.add('hscroll_label', button, (72, 40, 120, 20))
    first = ui.add('image', button, (160, 5, 42, 61), 'img_forward_0')
    fw.fn('stock_more_speed', None, C.c_void_p)(win.addr)
    if geom == 'GEOM_IPOD':
        assert row.box[3] == 32 and icon.visible and speed.box == (72, 5, 120, 30)
        return
    inset = rows_['geom_vals'][names[geom]][names['V_TEXT_X']]
    assert row.box[3] == O['SET_STOCK_ROW'] and not icon.visible
    assert button.box[0] + speed.box[0] == inset and speed.box[0] + speed.box[2] == first.box[0] - 2
    assert value.box[0] == speed.box[0] and value.box[1] > speed.box[1] + speed.box[3] - 1
    assert all('style:normal:font_name' in w.props for w in (speed, value))


# The settings lists (docs/ipod.md#settings): stock's 78 px rows of a 335 x 70 button at x 20, mapped to the set's.
SET_X, SET_W, STOCK_ROW, STOCK_BODY = O['SET_STOCK_X'], O['SET_STOCK_W'], O['SET_STOCK_ROW'], O['SET_STOCK_BODY']
SET_EDGE, SET_TEXT_X, SET_ICON_X, SET_GAP = O['SET_EDGE'], O['SET_TEXT_X'], O['SET_ICON_X'], O['SET_GAP']


class SettingsList:
    """A settings page's list_view (its asset's default_item_height the set's settings row) and scroll view, laid out
    by ipod_list_layout around a stand-in for stock's list layouter, which notes the list's item_height it saw."""
    def __init__(self, fw, ui, st, geometry, stand_in, geom='GEOM_IPOD', theme=b'0', view='scroll_view_wireless',
                 item_height=None, row_height=0, window='wireless_page'):
        names, rows = geometry
        Config(fw, THEME=theme)
        st('geom').value = names[geom]
        self.g = {n: rows['geom_vals'][names[geom]][names[n]] for n in ('V_SET_ROW', 'V_SET_ICON', 'V_STREAM_ROW', 'V_TEXT_X')}
        self.ui, self.fw = ui, fw
        self.win = ui.window(window)
        self.list = ui.add('list_view', self.win, (0, 30, 375, 290))
        struct.pack_into('<ii', self.list.block, O['ROW_HEIGHT'], row_height,
                         self.g['V_SET_ROW'] if item_height is None else item_height)
        self.view = ui.add('scroll_view', self.list, (0, 0, 375, 290), view)
        self.seen = (C.c_int * 1)(-1)
        code = (b'\xa1' + struct.pack('<Q', self.list.addr + O['ROW_HEIGHT']) + b'\xa3' + struct.pack('<Q', C.addressof(self.seen)) +
                b'\xb8' + struct.pack('<i', 6) + b'\xc3')  # mov eax, [item_height]; mov [seen], eax; mov eax, 6; ret
        stand_in(LIST_LAYOUT, code)
        self.run = fw.fn('ipod_list_layout', C.c_int, C.c_void_p, C.c_void_p)

    def row(self, *children, button=(SET_X, 0, SET_W, STOCK_BODY), h=STOCK_ROW, kind='button'):
        """A stock row: list_item > button > children, each (kind, box, name, image)."""
        item = self.ui.add('list_item', self.view, (0, 0, 375, h))
        b = self.ui.add(kind, item, button)
        for k, box, name, *image in children:
            w = self.ui.add(k, b, box, name)
            if image:
                w.props['image'] = image[0]
        return item

    def __call__(self):
        assert self.run(None, self.view.addr) == 6  # stock's answer
        return self.seen[0]


STOCK_ICON = ('image', (10, 9, O['SET_STOCK_ICON'], 52), 'icon', b'system_wifi')
STOCK_LABEL = ('hscroll_label', (72, 0, 260, STOCK_BODY), 'label')
STOCK_INTO = ('image', (276, 10, 50, 50), 'into', b'list_into')


def centre(box):
    return box[1] + box[3] / 2


def test_a_settings_row_takes_the_sets_height_icon_text_and_trailing_columns(fw, st, geometry, stand_in):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in)
    item = lst.row(STOCK_ICON, STOCK_LABEL, STOCK_INTO)
    assert lst() == 0
    row_h, icon_px = lst.g['V_SET_ROW'], lst.g['V_SET_ICON']
    button, (icon, label, into) = item.children[0], item.children[0].children
    assert item.box[3] == row_h and button.box == (0, 0, 375, row_h)  # the button spans the row
    assert icon.box == (SET_ICON_X, (row_h - icon_px) // 2, icon_px, icon_px)
    assert [a[1] for _, a in fw.calls('image_set_draw_type')] == [O['IMAGE_DRAW_SCALE_DOWN']]
    assert label.box[0] == icon.box[0] + icon_px + SET_GAP and (label.box[1], label.box[3]) == (0, row_h)  # full height fills
    assert into.box[0] + into.box[2] == 375 - SET_EDGE and centre(into.box) == row_h / 2  # shorter: its centre kept
    assert label.box[0] + label.box[2] <= 375 - SET_TEXT_X


def test_a_settings_row_is_mapped_once(fw, st, geometry, stand_in):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in)
    item = lst.row(STOCK_ICON, STOCK_LABEL, STOCK_INTO)
    lst()
    boxes = [c.box for c in item.children[0].children]
    lst()
    assert [c.box for c in item.children[0].children] == boxes


def test_a_hidden_row_is_mapped_after_it_shows(fw, st, geometry, stand_in):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in)
    item = lst.row(STOCK_ICON, STOCK_LABEL, STOCK_INTO)
    item.visible = 0
    lst()
    assert item.box[3] == lst.g['V_SET_ROW'] and item.children[0].box == (SET_X, 0, SET_W, STOCK_BODY)  # stock skips it
    item.visible = 1
    lst()
    assert item.children[0].box == (0, 0, 375, lst.g['V_SET_ROW'])


def test_rows_another_builder_made_keep_their_own(fw, st, geometry, stand_in):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in)
    wide = lst.row(STOCK_LABEL, button=(0, 0, 375, STOCK_BODY))
    tall = lst.row(STOCK_LABEL, h=90)
    view_row = lst.row(STOCK_LABEL, kind='view')
    speed = lst.row(STOCK_LABEL, ('image', (160, 5, 42, 61), 'img_forward_0'))  # More's speed row: stock_more_speed's
    lst()
    assert [r.box[3] for r in (wide, tall, view_row, speed)] == [STOCK_ROW, 90, STOCK_ROW, STOCK_ROW]
    assert [r.children[0].box[0] for r in (wide, view_row, speed)] == [0, SET_X, SET_X]
    assert tall.children[0].box == (0, 0, 375, 90)  # a stock button in a taller row: mapped at the row's own height


@pytest.mark.parametrize('geom, theme, doubles', [
    ('GEOM_IPOD', MINIMAL, False),  # SET_ROW's 68 px rows hold a title over a detail line
    ('GEOM_CLASSIC_LIGHT', CLASSIC, True)])  # Classic Light's 32 px settings rows (theme.xml) don't
def test_a_detail_line_doubles_a_short_row(fw, st, geometry, stand_in, geom, theme, doubles):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in, geom=geom, theme=b'%d' % theme)
    detail = lst.row(('hscroll_label', (72, 5, 260, 30), 'name'), ('hscroll_label', (72, STOCK_BODY // 2, 260, 30), 'quality'))
    single = lst.row(('hscroll_label', (72, 5, 260, 30), 'name'))
    lst()
    row = lst.g['V_SET_ROW']
    # a row too short for stock's detail line (Bluetooth's Quality) takes two rows' height, or it would run into
    # the next row; a row with no detail line keeps the set's
    assert (detail.box[3], single.box[3]) == ((2 * row if doubles else row), row)


def test_classics_rows_drop_their_icons_and_keep_a_pickers_mark(fw, st, geometry, stand_in):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in, geom='GEOM_CLASSIC_LIGHT', theme=b'1')
    assert lst.g['V_SET_ICON'] == 0
    plain = lst.row(STOCK_ICON, STOCK_LABEL, STOCK_INTO)
    device = lst.row(('image', (10, 10, 50, 50), 'device', b'bt_headset'), STOCK_LABEL)
    picker = lst.row(('image', (10, 9, 52, 52), 'mark', b'select'), STOCK_LABEL)
    lst()
    row = lst.g['V_SET_ROW']
    icon, label, into = plain.children[0].children
    assert not icon.visible and label.box[0] == SET_TEXT_X and centre(into.box) == row / 2
    assert not device.children[0].children[0].visible and device.children[0].children[1].box[0] == SET_TEXT_X
    mark = picker.children[0].children[0]
    assert mark.visible and mark.box == (375 - SET_EDGE - 50, 0, 50, row)  # in the column of stock's own marks


def test_an_icon_sized_image_goes_wherever_it_sits_in_a_set_without_icons(fw, st, geometry, stand_in):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in, geom='GEOM_CLASSIC_LIGHT', theme=b'1')
    item = lst.row(STOCK_LABEL, ('image', (200, 9, O['SET_STOCK_ICON'], 52), 'badge', b'bt_codec'))
    lst()
    label, badge = item.children[0].children
    assert not badge.visible and label.visible


# Stock's More rows' kinds (PLAYMORE_ROWS), out of index order here, so a row's kind is not its place
MORE_KINDS = [6, 0, 3, 7, 2, 5, 1, 4]


@pytest.mark.parametrize('theme, geom, count, marks', [
    (CLASSIC, 'GEOM_CLASSIC_LIGHT', O['PLAYMORE_COUNT'], True),  # its chevron on the selected row alone
    (CLASSIC, 'GEOM_CLASSIC_LIGHT', O['PLAYMORE_COUNT'] - 1, False),  # not More's own rows: none read
    (MINIMAL, 'GEOM_IPOD', O['PLAYMORE_COUNT'], False)])  # a chevron on every drill row: nothing to mark
def test_mores_rows_that_open_a_screen_end_their_text_before_the_selected_chevron(fw, st, geometry, stand_in, theme, geom,
                                                                                  count, marks):
    assert sorted(MORE_KINDS) == list(range(O['PLAYMORE_COUNT']))
    stand_in(O['PLAYMORE_ROWS'], struct.pack(f'<{len(MORE_KINDS)}i', *MORE_KINDS))
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in, geom=geom, theme=b'%d' % theme, view='scroll_view_more',
                       window='playmore_page')
    items = [lst.row(STOCK_LABEL) for _ in range(count)]
    fw.fn('stock_more_into', None, C.c_void_p)(lst.win.addr)  # as More's page is built, then laid out
    lst()
    # a row whose kind opens a screen (PLAYMORE_INTO_FIRST to _LAST), wherever it sits, ends its text before the
    # theme's chevron; the others run to the text margin
    chevron = theme_of(theme)['chevron_x']
    ends = [item.children[0].children[0].box[0] + item.children[0].children[0].box[2] for item in items]
    opens = [O['PLAYMORE_INTO_FIRST'] <= MORE_KINDS[i] <= O['PLAYMORE_INTO_LAST'] for i in range(count)]
    assert ends == [chevron - 1 if marks and opening else 375 - SET_TEXT_X for opening in opens]
    assert not any(c.visible for item in items for c in item.children[1:])  # no mark is drawn


def test_a_wide_trailing_image_sets_the_right_column(fw, st, geometry, stand_in):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in)
    item = lst.row(STOCK_LABEL, ('image', (230, 10, 50, 50), 'switch', b'switch_on'), ('image', (290, 10, 50, 50), 'into', b'list_into'))
    lst()
    _, switch, into = item.children[0].children
    # the rightmost trailing image ends SET_EDGE from the row's edge; the others move with it
    assert into.box[0] + into.box[2] == 375 - SET_EDGE and into.box[0] - switch.box[0] == 290 - 230


def test_streamings_rows_take_its_own_height_and_the_themes_art_columns(fw, st, geometry, stand_in):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in, geom='GEOM_CLASSIC_LIGHT', theme=b'1', view='scroll_view_streamsset',
                       item_height=0)
    item = lst.row(STOCK_ICON, STOCK_LABEL)
    lst()
    t = next(t for t in THEMES if t['index'] == CLASSIC)
    row = lst.g['V_STREAM_ROW']
    icon, label = item.children[0].children
    assert item.box[3] == row and row != lst.g['V_SET_ROW']
    assert icon.visible and (icon.box[0], centre(icon.box)) == (t['art_x'], row / 2) and icon.box[2] == O['SET_ICON']
    assert label.box[0] == t['art_text_x'] - 2


def test_another_list_is_stocks_alone(fw, st, geometry, stand_in):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in, item_height=55)
    item = lst.row(STOCK_ICON, STOCK_LABEL)
    assert lst() == 0
    assert item.box[3] == STOCK_ROW and item.children[0].box == (SET_X, 0, SET_W, STOCK_BODY)


@pytest.mark.parametrize('tile, grid', [(110, 1), (335, 0)])
def test_a_grid_keeps_its_rows_height_under_a_forced_item_height(fw, st, geometry, stand_in, tile, grid):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in, item_height=55, row_height=32)
    for _ in range(2):
        lst.row(button=(0, 0, tile, 150), h=160)
    # the artist page's albums: stock forces no item_height on its 160 px rows; a list's rows take it
    assert lst() == (0 if grid else 32)
    assert struct.unpack_from('<i', lst.list.block, O['ROW_HEIGHT'])[0] == 32  # put back after


def test_a_settings_list_takes_the_themes_top(fw, st, geometry, stand_in):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in)
    lst.list.box = (0, 0, 375, 290)
    lst()
    assert lst.list.box[1] == next(t for t in THEMES if t['index'] == MINIMAL)['set_top']


# Hold Return's Now Playing, and the boot resume.
LATCH = O['RETURN_RELEASE_LATCH']


class Hold:
    """Hold Return on top of ui's windows: navigator calls move the window stack as stock's do."""
    def __init__(self, fw, ui, stand_in, hold_home=b'0', theme=b'0'):
        self.fw, self.ui = fw, ui
        Config(fw, HOLDRETURN=hold_home, THEME=theme)
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        stand_in(LATCH, b'\1')  # stock's long Return armed it
        self.opened = []
        fw.mock('navigator_switch_to_with_context', self.switch)
        fw.mock('navigator_back', lambda *_: ui.wm.children.pop() and 0)
        fw.mock('navigator_back_to_home', lambda *_: ui.wm.children.__delitem__(slice(1, None)))
        self.run = fw.fn('compact_now_playing', C.c_int)
        self.lands = True

    def switch(self, name, ctx, *_):
        self.opened.append((C.string_at(name).decode(), struct.unpack('<4i', C.string_at(ctx, 16))))
        if self.lands:
            self.ui.window(C.string_at(name).decode())

    def __call__(self):
        assert self.run() == 0
        return [w.name for w in self.ui.wm.children]

    @staticmethod
    def latch():
        return C.c_ubyte.from_address(LATCH).value


def test_hold_return_opens_now_playing_over_the_page_and_leaves_the_latch_to_swallow_its_release(fw, st, stand_in):
    ui = Ui(fw)
    ui.window('home_page')
    songs = ui.window('localmusic_page')
    hold = Hold(fw, ui, stand_in)
    assert hold() == ['home_page', 'localmusic_page', 'playing_page']
    assert hold.opened == [('playing_page', (0, 0, 0xff, 2))]  # stock's car-mode context: the queue as it is
    assert st('np_origin', C.c_void_p).value == songs.addr
    assert hold.latch() == 1  # stock's filter swallows the hold's release; cleared, it went back from Now Playing


def test_hold_return_on_now_playing_goes_back_to_the_page_it_opened_over(fw, st, stand_in):
    ui = Ui(fw)
    ui.window('home_page'), ui.window('localmusic_page')
    hold = Hold(fw, ui, stand_in)
    hold()
    assert hold() == ['home_page', 'localmusic_page'] and not st('np_origin', C.c_void_p).value


def test_hold_return_on_now_playing_opened_elsewhere_goes_home(fw, st, stand_in):
    ui = Ui(fw)
    ui.window('home_page'), ui.window('localmusic_page')
    hold = Hold(fw, ui, stand_in)
    hold()
    ui.wm.children.insert(2, W(ui, 'window', None, name='folder_page'))  # another page came between
    assert hold() == ['home_page']


def test_now_playing_not_opened_by_a_hold_goes_home(fw, st, stand_in):
    ui = Ui(fw)
    ui.window('home_page'), ui.window('localmusic_page'), ui.window('playing_page')
    hold = Hold(fw, ui, stand_in)
    assert hold() == ['home_page'] and not fw.calls('navigator_back')


@pytest.mark.parametrize('theme', [b'0', b'1'])
def test_hold_return_home_goes_home_from_any_page_but_home(fw, st, stand_in, theme):
    ui = Ui(fw)
    ui.window('home_page'), ui.window('localmusic_page'), ui.window('folder_page')
    hold = Hold(fw, ui, stand_in, hold_home=b'1', theme=theme)
    st('np_origin', C.c_void_p).value = 0x1234
    assert hold() == ['home_page'] and not st('np_origin', C.c_void_p).value
    assert hold() == ['home_page', 'playing_page']  # on Home itself it still opens Now Playing


def test_hold_return_does_nothing_dark_or_while_windows_move(fw, st, stand_in):
    ui = Ui(fw)
    ui.window('home_page'), ui.window('localmusic_page')
    hold = Hold(fw, ui, stand_in)
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    assert hold() == ['home_page', 'localmusic_page']
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    fw.mock('window_manager_is_animating', lambda *_: 1)
    assert hold() == ['home_page', 'localmusic_page'] and hold.opened == []


@pytest.mark.parametrize('ctx, page, start', [
    ((0x51000, 3, 0xf001, 2), 1, None),  # car mode plays: stock's page
    ((0x51000, 3, 0xf001, 0), 0, (0x51000, 3, 0xf001, 0)),  # otherwise the paused start, without the page
    ((0x51000, 3, 0xff, 0), 0, None)])  # no class: nothing to resume
def test_the_boot_resume_starts_paused_without_opening_now_playing(fw, ctx, page, start):
    context = (C.c_int * 4)(*ctx)
    fw.fn('ringnav_boot', None, C.c_char_p, C.c_void_p)(b'playing_page', context)
    assert len(fw.calls('navigator_to_with_context')) == page
    assert [a[:4] for _, a in fw.calls('player_start')] == ([start] if start else [])


@pytest.mark.parametrize('geom, asks', [('GEOM_IPOD', 1), ('GEOM_CLASSIC_LIGHT_TEXT', 0)])
def test_an_unknown_artist_reads_stocks_words_only_where_rows_keep_stocks_art(fw, st, geometry, geom, asks):
    names, _ = geometry
    st('geom').value = names[geom]
    fw.mock('getFormatString', lambda key, text, size, *_: C.memmove(text, b'Unknown Artist\0', 15) and 0)
    out = C.create_string_buffer(64)  # the row's second line, empty until written
    fw.fn('ringnav_song_unknown', None, C.c_char_p, C.c_char_p, C.c_int)(b'player_unknown', out, 64)
    # stock's row reads the language's words; a text-only set's second line says only what the record says
    assert out.value == (b'Unknown Artist' if asks else b'')
    assert [(C.string_at(a[0]), a[2]) for _, a in fw.calls('getFormatString')] == ([(b'player_unknown', 64)] if asks else [])


# Records: stSongInfo as stock's library fills them.
libc = C.CDLL(None)
libc.mmap.restype, libc.mmap.argtypes = C.c_void_p, [C.c_void_p, C.c_size_t, C.c_int, C.c_int, C.c_int, C.c_long]
libc.munmap.argtypes = [C.c_void_p, C.c_size_t]
FIELDS = {k: O['REC_' + k.upper()] for k in ('name', 'path', 'title', 'album', 'artist', 'album_artist')}


class Records:
    """A pointer field is 4 bytes on the Q2 and 8 here, so each read takes the next field's low half as its high one.
    So a string goes where that does no harm: a low one (under 4 GB, its high half 0) keeps the next field readable,
    an aligned one (on a 4 GB boundary, its low half 0, in a page of its own at a high half of this file's:
    conftest.high_halves) the one before. A field and the next both set: the first low, the second aligned. A lone
    name or path is low, so the title after it stays unset; any other lone field is aligned, so the title or path
    before it stays as it was."""
    LOW = ('name', 'path')
    HIGHS = high_halves(0x100)

    def __init__(self):
        self.maps, self.used, self.high = [], 0, iter(self.HIGHS)
        self.low = self.map(0, 1 << 20, 0x40)  # MAP_32BIT

    def map(self, at, size, flags):
        p = libc.mmap(at, size, 3, 0x22 | flags, -1, 0)
        assert p and p != C.c_void_p(-1).value and (not at or p == at), f'{at:#x} is taken'
        self.maps.append((p, size))
        return p

    def put(self, at, data):
        C.memmove(at, data + b'\0', len(data) + 1)
        return at

    def take(self, n):
        at = self.low + self.used
        self.used += n + 15 & ~15
        return at

    def string(self, data, low):
        if low:
            return self.put(self.take(len(data) + 1), data)
        return self.put(self.map(next(self.high) << 32, 0x1000, 0x100000), data)  # MAP_FIXED_NOREPLACE

    def song(self, kind=8, id_=0, cue=0, disc=0, **fields):
        r = self.take(0x60)
        values = {FIELDS[k]: (k, v) for k, v in fields.items() if v is not None}
        for o in sorted(values):
            name, value = values[o]
            assert not (o - 4 in values and o + 4 in values), 'three neighbours: no layout'
            low = o + 4 in values or (o - 4 not in values and name in self.LOW)
            C.c_uint64.from_address(r + o).value = self.string(value, low)
        for offset, value in ((O['REC_TYPE'], kind), (O['REC_ID'], id_), (O['REC_CUE_START'], cue), (O['REC_DISC'], disc)):
            C.c_int.from_address(r + offset).value = value
        return r

    def close(self):
        for p, size in self.maps:
            libc.munmap(p, size)


@pytest.fixture
def records():
    r = Records()
    yield r
    r.close()


def field(r, name):
    p = C.c_void_p.from_address(r + FIELDS[name]).value
    return C.string_at(p) if p else None


# The queue menu's checks: what the hold menu was opened on must still be there when an action runs.
# the hold menu's kinds: an album row (all albums, an artist's, composer's or genre's) is QM_ALBUM, a row of the
# artist, composer and genre lists QM_GROUP
QM_SONG, QM_ALBUM, QM_GROUP, QM_COVERFLOW, QM_COVERALBUM, QM_PLAYING, QM_PLAYLIST = 1, 2, 3, 5, 6, 7, 8


def ctx_ptr(fw, name):
    return C.c_uint.from_address(C.addressof(fw.var('fw_ctx_' + name, C.c_char)))


class Menu:
    """The hold menu as its opener leaves it: on row idx of a list, by its count, record and browsing hashes."""
    def __init__(self, fw, st, kind, rows, idx, cls=0):
        self.fw, self.st = fw, st
        self.record = fw.fn('qm_record', C.c_void_p)
        rec_hash = fw.fn('navigation__rec_hash', C.c_uint, C.c_void_p)
        st('qm_kind').value, st('qm_idx', C.c_uint).value, st('qm_rows', C.c_uint).value = kind, idx, len(rows)
        st('qm_cls', C.c_uint).value = cls
        st('qm_hash', C.c_uint).value = rec_hash(rows[idx])
        st('qm_browse', C.c_uint).value = fw.fn('navigation__browse_hash', C.c_uint)()


def song_list(fw, records, n=3):
    deques = Deques(fw)
    rows = [records.song(name=b'%d.flac' % i, cue=i) for i in range(n)]
    handle = deques.new(rows)
    ctx_ptr(fw, 'p_deque_showlist').value = handle
    return deques, handle, rows


def test_the_menus_row_is_found_while_the_list_stands(fw, st, records):
    deques, handle, rows = song_list(fw, records)
    menu = Menu(fw, st, QM_SONG, rows, 1)
    assert menu.record() == rows[1]


@pytest.mark.parametrize('change', ['row', 'count', 'browsing', 'index'])
def test_the_menus_row_is_gone_once_the_list_changes(fw, st, records, change):
    deques, handle, rows = song_list(fw, records)
    menu = Menu(fw, st, QM_SONG, rows, 1)
    if change == 'row':
        deques[handle][1] = records.song(name=b'other.flac')
    elif change == 'count':
        deques[handle].append(records.song(name=b'3.flac'))
    elif change == 'browsing':
        fw.var('fw_ctx_g_folder_path', C.c_char * 8).value = b'/mnt/mmc'
    else:
        st('qm_idx', C.c_uint).value = 3
    assert not menu.record()


def test_the_menus_list_by_its_kind(fw, st, records):
    deques = Deques(fw)
    lists = {name: deques.new([records.song(name=name.encode())]) for name in ('p_deque_showlist', 'p_deque_playlist',
                                                                              'mcl_pdeqplaylist')}
    for name, handle in lists.items():
        ctx_ptr(fw, name).value = handle
    qm_list = fw.fn('navigation__qm_list', C.c_void_p)
    for kind, name in ((QM_SONG, 'p_deque_showlist'), (QM_ALBUM, 'p_deque_showlist'), (QM_PLAYLIST, 'p_deque_playlist'),
                       (QM_PLAYING, 'mcl_pdeqplaylist')):
        st('qm_kind').value = kind
        assert qm_list() == lists[name]


@pytest.mark.parametrize('page', ['mp', 'rt', 'other'])
def test_a_coverflow_kind_menu_takes_the_pages_own_tracks(fw, st, records, page):
    ui = Ui(fw)
    deques = Deques(fw)
    rows = [records.song(name=b'%d.flac' % i) for i in range(2)]
    top = ui.window('mostplayed_page')
    tracks = deques.new(rows)
    if page != 'other':
        st(f'{page}_page', C.c_void_p).value, st(f'{page}_list', C.c_void_p).value = top.addr, tracks
    ctx_ptr(fw, 'p_deque_showlist').value = deques.new(rows)  # Coverflow's kinds never read the browse list
    menu = Menu(fw, st, QM_COVERFLOW, rows, 1)
    assert menu.record() == (rows[1] if page != 'other' else None)
    ui.wm.children.pop()
    assert not menu.record()  # its page gone


def test_now_playings_menu_holds_while_the_track_and_queue_stand(fw, st, records, mcl):
    deques = Deques(fw)
    rows = [records.song(name=b'%d.flac' % i) for i in range(3)]
    queue = deques.new(rows)
    ctx_ptr(fw, 'mcl_pdeqplaylist').value = queue
    mcl.pos, mcl.type = 1, 0xf001
    menu = Menu(fw, st, QM_PLAYING, rows, 1)
    st('qm_queue', C.c_void_p).value = queue
    st('qm_queue_hash', C.c_uint).value = fw.fn('queue_hash', C.c_uint, C.c_void_p)(queue)
    assert menu.record() == rows[1]
    mcl.pos = 2  # the next track
    assert not menu.record()
    mcl.pos, mcl.type = 1, 0x0200  # an online queue
    assert not menu.record()
    mcl.type = 1  # a folder's
    assert menu.record() == rows[1]
    fw.mock('airplayGetFlag', lambda *_: 2)  # AirPlay plays
    assert not menu.record()
    fw.mock('airplayGetFlag', lambda *_: 0)
    deques[queue][0] = records.song(name=b'new.flac')  # the queue changed under it
    assert not menu.record()


def test_play_hold_release_matches_only_its_own_press(fw, st):
    status = C.create_string_buffer(O['INPUT_KEYS'] + O['INPUT_KEY_COUNT'] * O['INPUT_KEY_SIZE'])
    play = O['INPUT_KEYS'] + 3 * O['INPUT_KEY_SIZE']
    struct.pack_into('<i', status, play, O['KEY_PLAY'])
    struct.pack_into('<Q', status, play + O['INPUT_KEY_TIME'], 123456)
    fw.mock('window_manager_get_input_device_status', lambda *_: C.addressof(status))
    released = fw.fn('navigation__hold_released', C.c_int)
    press = st('qm_press', C.c_ulonglong)
    press.value = 123456
    assert released() == 1 and press.value == 0  # the held press's own release: swallowed
    press.value = 123000
    assert released() == 0 and press.value == 0  # a later press's: passes
    press.value = 123456
    struct.pack_into('<i', status, play, O['KEY_RETURN'])
    assert released() == 0 and press.value == 0  # no Play key in the device's state


def test_now_playings_more_while_it_is_open(fw, st):
    ui = Ui(fw)
    win = ui.window('playing_page')
    more = ui.add('image', ui.add('view', win), name='img_more')
    np_more = fw.fn('navigation__np_more', C.c_void_p)
    assert not np_more()
    st('np_win', C.c_void_p).value = win.addr
    assert np_more() == more.addr


def test_the_menu_closes_on_return_or_its_arrow_and_forgets_a_closed_dialog(fw, st):
    ui = Ui(fw)
    dialog = ui.window('sortselect_dialog')
    st('qm_dialog', C.c_void_p).value = dialog.addr
    back = fw.fn('navigation__qm_back', C.c_int, C.c_void_p, C.c_void_p)
    assert back(dialog.addr, event(O['EVT_KEY_UP'], O['KEY_CENTER'])) == 0 and st('qm_dialog', C.c_void_p).value
    assert back(dialog.addr, event(O['EVT_KEY_UP'], O['KEY_RETURN'])) == STOP
    assert not st('qm_dialog', C.c_void_p).value and [a[0] for _, a in fw.calls('window_close')] == [dialog.addr]
    st('qm_dialog', C.c_void_p).value = dialog.addr
    assert back(dialog.addr, event(EVT_CLICK)) == STOP  # the title bar's arrow
    gone = fw.fn('navigation__qm_gone', C.c_int, C.c_void_p, C.c_void_p)
    other = ui.window('msginfo_dialog')
    st('qm_dialog', C.c_void_p).value = dialog.addr
    gone(other.addr, None)
    assert st('qm_dialog', C.c_void_p).value == dialog.addr
    gone(dialog.addr, None)
    assert not st('qm_dialog', C.c_void_p).value
    fw.fn('navigation__qm_close', None)()
    assert len(fw.calls('window_close')) == 2  # nothing more to close


def queried(fw):
    """Each library query run: (its name, its string arguments)."""
    names = ('getMusicByAlbum', 'getMusicBySonger', 'getMusicByAlbumArtist', 'getMusicByComposer', 'getMusicByGenre',
             'getMusicByAlbumAndSonger', 'getMusicByAlbumAndAlbumSonger', 'getMusicByAlbumAndComposer',
             'getMusicByAlbumAndGenre', 'getMusicByPlayList')
    return [(n, a[0]) if n == 'getMusicByPlayList' else
            (n, *(C.string_at(v) if v else None for v in a[:2 if 'AlbumAnd' in n else 1])) for n, a in fw.calls() if n in names]


def put(records, r, offset, value):
    """A string at r + offset that no stock field name covers (+0x1c a genre row's genre, +0x20 a composer's)."""
    C.c_uint64.from_address(r + offset).value = records.string(value, low=False)


@pytest.mark.parametrize('cls, artist_type, query', [
    (0xf004, 0, ('getMusicBySonger', b'Miles Davis')), (0xf004, 1, ('getMusicByAlbumArtist', b'Miles Davis')),
    (0xf005, 0, ('getMusicByComposer', b'Gil Evans')), (0xf006, 0, ('getMusicByGenre', b'Jazz'))])
def test_a_group_rows_menu_runs_the_query_its_row_opens(fw, st, records, cls, artist_type, query):
    fw.var('fw_ctx_g_artist_type', C.c_int).value = artist_type
    r = records.song(artist=b'Miles Davis') if cls == 0xf004 else records.song()
    if cls != 0xf004:
        put(records, r, {0xf005: 0x20, 0xf006: 0x1c}[cls], {0xf005: b'Gil Evans', 0xf006: b'Jazz'}[cls])
    st('qm_kind').value, st('qm_cls', C.c_uint).value = QM_GROUP, cls
    fw.fn('navigation__qm_query', C.c_int, C.c_void_p)(r)
    assert queried(fw) == [query]


def test_an_unknown_rows_menu_asks_for_the_untagged(fw, st, records):
    r = records.song(id_=-1, artist=b'ignored')
    st('qm_kind').value, st('qm_cls', C.c_uint).value = QM_GROUP, 0xf004
    fw.fn('navigation__qm_query', C.c_int, C.c_void_p)(r)
    st('qm_kind').value, st('qm_cls', C.c_uint).value = QM_ALBUM, O['CLASS_ALBUMS']
    fw.fn('navigation__qm_query', C.c_int, C.c_void_p)(records.song(id_=-1, album=b'ignored'))
    fw.fn('navigation__qm_query', C.c_int, C.c_void_p)(records.song(album=b'Blue'))
    assert queried(fw) == [('getMusicBySonger', None), ('getMusicByAlbum', None), ('getMusicByAlbum', b'Blue')]


@pytest.mark.parametrize('cls, artist_type, unknown, query', [
    (0xff01, 0, 0, ('getMusicByAlbumAndSonger', b'Blue', b'Miles Davis')),
    (0xff01, 1, 0, ('getMusicByAlbumAndAlbumSonger', b'Blue', b'Miles Davis')),
    (0xff01, 0, 1, ('getMusicByAlbumAndSonger', b'Blue', None)),  # an Unknown artist's album
    (0xff03, 0, 0, ('getMusicByAlbumAndGenre', b'Blue', b'Jazz'))])
def test_an_albums_menu_under_a_group_keeps_the_group(fw, st, records, cls, artist_type, unknown, query):
    fw.var('fw_ctx_g_artist_type', C.c_int).value = artist_type
    info = fw.var('fw_ctx_g_local_classinfo_save', C.c_char * 912)
    info[0xa + (cls & 0xf) - 1] = bytes([unknown])
    if cls == 0xff03:
        r = records.song(album=b'Blue')
        put(records, r, 0x1c, b'Jazz')
    else:
        r = records.song(album=b'Blue', artist=b'Miles Davis')
    st('qm_kind').value, st('qm_cls', C.c_uint).value = QM_ALBUM, cls
    fw.fn('navigation__qm_query', C.c_int, C.c_void_p)(r)
    assert queried(fw) == [query]
    assert fw.calls(query[0])[0][1][2] == 0  # not the Unknown Album row (id -2)


@pytest.mark.parametrize('id_, unknown_album', [(-2, 1), (0, 0), (-1, 0)])
def test_an_artists_unknown_album_rows_menu_asks_for_its_untagged_albums_songs(fw, st, records, id_, unknown_album):
    r = records.song(id_=id_, artist=b'Miles Davis')
    st('qm_kind').value, st('qm_cls', C.c_uint).value = QM_ALBUM, O['CLASS_ARTIST_ALBUMS']
    fw.fn('navigation__qm_query', C.c_int, C.c_void_p)(r)
    # the artist's songs with no album tag: the query's own flag, as the row opens them (id -2, REC_ID's unknown album)
    ((name, args),) = [(n, a) for n, a in fw.calls() if n.startswith('getMusicByAlbumAnd')]
    assert (name, C.string_at(args[1]), args[2]) == ('getMusicByAlbumAndSonger', b'Miles Davis', unknown_album)


def test_a_playlists_menu_runs_its_playlist(fw, st, records):
    r = records.song(disc=7)
    st('qm_kind').value = QM_PLAYLIST
    fw.fn('navigation__qm_query', C.c_int, C.c_void_p)(r)
    assert queried(fw) == [('getMusicByPlayList', 7)]


def test_the_menus_album_id(fw, st, records):
    r = records.song(album=b'Blue', album_artist=b'Miles Davis', path=b'/m/Blue/1.flac')
    album_id = fw.fn('album_id', C.c_uint, C.c_void_p)(r)
    qm_album_id = fw.fn('qm_album_id', C.c_uint, C.c_void_p)
    st('qm_kind').value, st('qm_cls', C.c_uint).value = QM_ALBUM, O['CLASS_ALBUMS']
    assert qm_album_id(r) == album_id  # a Library Albums row: the album Coverflow rates
    st('qm_kind').value = QM_COVERALBUM
    assert qm_album_id(r) == album_id
    st('qm_kind').value, st('qm_cls', C.c_uint).value = QM_GROUP, 0xf004
    assert qm_album_id(r) == 0  # an artist's row rates no album
    # an artist's albums tab: the id ringnav_artist_list found for the row, while the tab is the one it read
    ids = (C.c_uint * 3)(11, 22, 33)
    st('qm_kind').value = QM_ALBUM  # its rows are albums
    st('qm_cls', C.c_uint).value, st('art_ids', C.c_void_p).value, st('art_n', C.c_uint).value = O['CLASS_ARTIST_ALBUMS'], C.addressof(ids), 3
    st('qm_rows', C.c_uint).value, st('qm_idx', C.c_uint).value = 3, 1
    assert qm_album_id(r) == 22
    st('qm_rows', C.c_uint).value = 4
    assert qm_album_id(r) == 0
    st('art_ids', C.c_void_p).value, st('art_n', C.c_uint).value = None, 0


# The Library's lists: Albums' split art keys, an album's own songs, the class lists and an artist's page.
CLASS_ALBUMS = O['CLASS_ALBUMS']


class Library:
    """Stock's list globals as Deques: the showlist, the staging deque its queries fill, and the cover tasks."""
    def __init__(self, fw):
        self.fw = fw
        self.d = Deques(fw)
        self.show, self.dir, self.covers = self.d.new(), self.d.new(), self.d.new()
        for name, handle in (('p_deque_showlist', self.show), ('tools_pdeq_directory', self.dir),
                             ('pdeq_albumcoverlist', self.covers)):
            ctx_ptr(fw, name).value = handle
        fw.mock('fw_deque_pop_back', lambda q, *_: self.d[q].pop() and 0)

    def staging(self):
        """The deque a query fills now: staged_into points tools_pdeq_directory at its own."""
        return self.d[ctx_ptr(self.fw, 'tools_pdeq_directory').value]

    def query(self, name, rows):
        """Stock's query name: rows into the staging deque, their count back."""
        self.fw.mock(name, lambda *_: self.staging().extend(rows) or len(rows))

    def tasks(self, *names):
        """Cover tasks as albumcoverinfo_init leaves them: both art flags set, the name a heap copy at +4."""
        strdup = self.fw.fn('strdup', C.c_void_p, C.c_char_p)
        self.task_blocks = [C.create_string_buffer(0x20) for _ in names]
        for block, name in zip(self.task_blocks, names):
            block[0] = block[1] = b'\1'
            C.c_void_p.from_buffer(block, 4).value = strdup(name)
        self.d[self.covers][:] = [C.addressof(b) for b in self.task_blocks]

    def task(self, i):
        block = self.task_blocks[i]
        return C.string_at(C.c_void_p.from_buffer(block, 4).value or 0), (block[0], block[1])


def albums(records):
    """Albums' rows as split leaves them, by name: a name of one album, then three albums of one name."""
    return [records.song(album=b'Blue', album_artist=b'Joni Mitchell', path=b'/m/Joni/Blue/01.flac', kind=4),
            records.song(album=b'Greatest Hits', album_artist=b'ABBA', path=b'/m/ABBA/GH/01.flac', kind=4),
            records.song(album=b'Greatest Hits', album_artist=b'Queen', path=b'/m/Queen/GH/01.flac', kind=4),
            records.song(album=b'Greatest Hits', path=b'/m/Various/GH/CD1/01.flac', kind=4)]


SPLIT_KEYS = [b'Blue', b'Greatest Hits\x1fABBA', b'Greatest Hits\x1fQueen', b'Greatest Hits\x1f/m/Various/GH']


def test_albums_of_one_name_key_their_art_by_album_artist_else_folder(fw, records):
    lib = Library(fw)
    lib.d[lib.show][:] = albums(records)
    art_name = fw.fn('ringnav_art_name', C.c_int, C.c_char_p, C.c_uint, C.c_uint, C.c_char_p)
    out = C.create_string_buffer(256)
    found = []
    for row in range(4):
        assert art_name(out, 256, row, b'Greatest Hits') == len(out.value)
        found.append(out.value)
    # a lone name keeps stock's key (and cached art); a shared one adds its album artist, else its folder (disc folders as one)
    assert found == SPLIT_KEYS
    art_name(out, 256, 4, b'Elsewhere')  # past the list: stock's own name
    assert out.value == b'Elsewhere'


def test_albums_cover_tasks_take_the_split_keys_and_find_their_art_again(fw, records):
    lib = Library(fw)
    lib.d[lib.show][:] = albums(records)
    lib.tasks(b'Blue', b'Greatest Hits', b'Greatest Hits', b'Greatest Hits')
    covers = fw.fn('ringnav_albumcovers', C.c_int, C.c_void_p)
    fw.mock('albumcoverinfo_init', lambda *_: 9)
    assert covers(lib.show) == 9  # stock's answer
    assert [lib.task(i) for i in range(4)] == [(b'Blue', (b'\1', b'\1'))] + [(k, (b'\0', b'\0')) for k in SPLIT_KEYS[1:]]
    for i in range(1, 4):
        lib.task_blocks[i][0] = b'\1'
    covers(lib.show)  # the same keys again: the art found stays
    assert [lib.task(i)[1][0] for i in range(4)] == [b'\1'] * 4
    assert len(fw.calls('fw_pthread_mutex_lock')) == len(fw.calls('fw_pthread_mutex_unlock')) == 2  # under aclist_mutex


@pytest.mark.parametrize('row, big, key, marked', [(2, 0, b'Greatest Hits\x1fQueen', (1, 0)), (2, 1, b'Greatest Hits\x1fQueen', (0, 1)),
                                                   (2, 0, b'Greatest Hits\x1fABBA', (0, 0)), (2, 2, b'Greatest Hits\x1fQueen', (0, 0)),
                                                   (4, 0, b'Greatest Hits\x1fQueen', (0, 0)), (-1, 0, b'x', (0, 0))])
def test_a_finished_cover_marks_only_the_row_still_holding_its_album(fw, row, big, key, marked):
    lib = Library(fw)
    lib.tasks(*SPLIT_KEYS)
    for b in lib.task_blocks:
        b[0] = b[1] = b'\0'
    fw.fn('ringnav_cover_done', None, C.c_int, C.c_int, C.c_char_p)(row, big, key)
    assert lib.task(2)[1] == tuple(bytes([m]) for m in marked)
    assert all(lib.task(i)[1] == (b'\0', b'\0') for i in (0, 1, 3))


@pytest.mark.parametrize('opened, album, shown', [(2, b'Greatest Hits', b'Greatest Hits\x1fQueen'), (2, b'greatest hits', b'Greatest Hits\x1fQueen'),
                                                  (2, b'Greatest', b'Greatest'), (0, b'Blue', b'Blue'), (7, b'Blue', b'Blue')])
def test_the_album_pages_big_cover_is_its_rows_split_key(fw, stand_in, opened, album, shown):
    lib = Library(fw)
    lib.tasks(*SPLIT_KEYS)
    stand_in(O['ALBUMINFO_ROW'], struct.pack('<i', opened))
    out = C.create_string_buffer(256)
    fw.fn('ringnav_art_header', C.c_int, C.c_char_p, C.c_uint, C.c_char_p, C.c_char_p)(out, 256, b'%s', album)
    assert out.value == shown


def classinfo(fw, name=b'', artist=b'', unknown=0):
    info = fw.var('fw_ctx_g_local_classinfo_save', C.c_char * 912)
    info[9] = bytes([unknown])
    C.memmove(C.addressof(info) + 0xd, name + b'\0', len(name) + 1)
    C.memmove(C.addressof(info) + 0x10d, artist + b'\0', len(artist) + 1)


class Localclass:
    """load_localclass_list: stock fills the showlist (stock_localclass_trampoline) and returns its size."""
    def __init__(self, fw, lib, rows=None):
        self.fw, self.lib, self.rows = fw, lib, rows or []
        fw.mock('stock_localclass_trampoline', lambda cls, *_: self.stock())
        self.load = fw.fn('ringnav_localclass', C.c_int, C.c_int)
        self.timers = []
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, ms)) or len(self.timers))

    def stock(self):
        self.lib.d[self.lib.show][:] = self.rows
        return len(self.rows)

    def __call__(self, cls):
        return self.load(cls)

    def shown(self, name='title'):
        return [field(r, name) for r in self.lib.d[self.lib.show]]


def gh_songs(records):
    """Every song named Greatest Hits, as 0xff10 loads them: Queen's and ABBA's."""
    return [records.song(title=b'%s %d' % (aa, i), album=b'Greatest Hits', album_artist=aa)
            for aa in (b'Queen', b'ABBA') for i in (1, 2)]


@pytest.mark.parametrize('opened, info, want', [
    ('row', (b'Greatest Hits', b'Queen', 0), [b'Queen 1', b'Queen 2']),  # its own album's songs
    ('row', (b'GREATEST HITS', b'Queen', 0), [b'Queen 1', b'Queen 2']),  # the name, case aside
    ('row', (b'Greatest Hits', b'ABBA', 0), None),  # classinfo another album's: every song of the name
    ('row', (b'Greatest Hits', b'Queen', 1), None),
    ('other class', (b'Greatest Hits', b'Queen', 0), None),  # Go to album from elsewhere: stock's list
    ('unknown', (b'Greatest Hits', b'Queen', 0), None)])
def test_an_albums_row_opens_only_its_own_albums_songs(fw, records, opened, info, want):
    lib = Library(fw)
    songs = gh_songs(records)
    row = records.song(album=b'Greatest Hits', artist=b'Queen', id_=-1 if opened == 'unknown' else 3, kind=4)
    C.c_uint64.from_address(row + FIELDS['album_artist']).value = records.string(b'Queen', low=False)
    fw.mock('stock_detail_trampoline', lambda *_: 5)
    detail = fw.fn('ringnav_album_detail', C.c_int, C.c_int, C.c_void_p)
    assert detail(CLASS_ALBUMS if opened != 'other class' else 0xf004, row) == 5
    classinfo(fw, *info)
    load = Localclass(fw, lib, songs)
    n = load(0xff10)
    assert load.shown() == (want or [field(s, 'title') for s in songs]) and n == len(load.shown())


def test_an_album_opened_from_elsewhere_forgets_the_last_albums_row(fw, records):
    lib = Library(fw)
    songs = gh_songs(records)
    row = records.song(album=b'Greatest Hits', artist=b'Queen', id_=3, kind=4)
    C.c_uint64.from_address(row + FIELDS['album_artist']).value = records.string(b'Queen', low=False)
    fw.mock('stock_detail_trampoline', lambda *_: 0)
    detail = fw.fn('ringnav_album_detail', C.c_int, C.c_int, C.c_void_p)
    classinfo(fw, b'Greatest Hits', b'Queen')
    load = Localclass(fw, lib, songs)
    detail(CLASS_ALBUMS, row)
    load(0xff10)
    assert load.shown() == [b'Queen 1', b'Queen 2']  # the Albums row's own album
    destroyed = len(fw.calls('fw_deque_destroy'))
    detail(0xf004, row)  # the same name and artist, opened from another list
    assert len(fw.calls('fw_deque_destroy')) == destroyed + 1  # the last album's copy goes
    load(0xff10)
    assert load.shown() == [field(s, 'title') for s in songs]  # every song of the name, as stock lists it


def album_db(fw, lib, records, albums):
    """The library database's albums, as coverflow_split's ALBUM_SQL reads them (id, album, songer, a track's path,
    album artist): stock's album_row makes each a record in the staging deque, split_row adds its album artist."""
    def album_row(ctx, n, v, names, *_):
        col = C.cast(v, C.POINTER(C.c_char_p))
        lib.staging().append(records.song(id_=int(col[0]), album=col[1], artist=col[2], path=col[3], kind=4))
        return 0
    def query(db, sql, row, ctx, *_):
        call = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.c_void_p, C.c_void_p)(row)
        names = (C.c_char_p * 5)(b'id', b'album', b'songer', b'fileurl', b'albumsonger')
        for a in albums:
            values = (C.c_char_p * 5)(*a)
            call(ctx, 5, C.addressof(values), C.addressof(names))
        return len(albums)
    fw.mock('album_row', album_row)
    fw.mock('toolsQueryDbTable', query)


def test_albums_from_stocks_names_to_one_albums_songs_and_cover(fw, stand_in, records):
    lib = Library(fw)
    album_db(fw, lib, records, [(b'1', b'Blue', b'Joni Mitchell', b'/m/Joni/Blue/01.flac', b'Joni Mitchell'),
                                (b'2', b'Greatest Hits', b'ABBA', b'/m/ABBA/GH/01.flac', b'ABBA'),
                                (b'3', b'Greatest Hits', b'Queen', b'/m/Queen/GH/01.flac', b'Queen'),
                                (b'4', b'Greatest Hits', b'Various', b'/m/Various/GH/CD1/01.flac', None)])
    # stock's cover tasks, one per row by its name, as albumcoverinfo_init makes them: the art found by name
    fw.mock('albumcoverinfo_init', lambda q, *_: lib.tasks(*(field(r, 'album') for r in lib.d[q])) or 9)
    # Albums: getAllAlbum's rows by name alone, each split into that name's albums in the database's order
    load = Localclass(fw, lib, [records.song(id_=1, album=b'Blue', kind=4), records.song(id_=2, album=b'Greatest Hits', kind=4)])
    assert load(CLASS_ALBUMS) == 4
    shown = list(lib.d[lib.show])
    assert [(field(r, 'album'), field(r, 'album_artist')) for r in shown] == [
        (b'Blue', b'Joni Mitchell'), (b'Greatest Hits', b'ABBA'), (b'Greatest Hits', b'Queen'), (b'Greatest Hits', None)]
    # the renderer's art name for each row: a lone name stock's own, a shared one with its album artist, else folder
    art_name = fw.fn('ringnav_art_name', C.c_int, C.c_char_p, C.c_uint, C.c_uint, C.c_char_p)
    out = C.create_string_buffer(256)
    keys = []
    for i, r in enumerate(shown):
        art_name(out, 256, i, field(r, 'album'))
        keys.append(out.value)
    assert keys == SPLIT_KEYS
    # the list's cover tasks take those keys: Blue's art, found by its name, stays found; the others look again
    assert fw.fn('ringnav_albumcovers', C.c_int, C.c_void_p)(lib.show) == 9
    assert [lib.task(i) for i in range(4)] == [(b'Blue', (b'\1', b'\1'))] + [(k, (b'\0', b'\0')) for k in SPLIT_KEYS[1:]]
    # Queen's cover comes in for its row; the same key finishing for ABBA's row marks nothing there
    done = fw.fn('ringnav_cover_done', None, C.c_int, C.c_int, C.c_char_p)
    done(2, 0, keys[2])
    done(1, 0, keys[2])
    assert [lib.task(i)[1] for i in (1, 2)] == [(b'\0', b'\0'), (b'\1', b'\0')]
    # Queen's row opens its own songs, under the big cover its row's key names
    fw.mock('stock_detail_trampoline', lambda *_: 0)
    fw.fn('ringnav_album_detail', C.c_int, C.c_int, C.c_void_p)(CLASS_ALBUMS, shown[2])
    classinfo(fw, b'Greatest Hits', b'Queen')
    load.rows = gh_songs(records)
    load(0xff10)
    assert load.shown() == [b'Queen 1', b'Queen 2']
    stand_in(O['ALBUMINFO_ROW'], struct.pack('<i', 2))
    fw.fn('ringnav_art_header', C.c_int, C.c_char_p, C.c_uint, C.c_char_p, C.c_char_p)(out, 256, b'%s', b'Greatest Hits')
    assert out.value == keys[2]


def test_an_unknown_row_whose_query_finds_nothing_goes(fw, records):
    lib = Library(fw)
    rows = [records.song(artist=b'ABBA', kind=4), records.song(artist=b'Queen', kind=4), records.song(id_=-1, kind=4)]
    load = Localclass(fw, lib, rows)
    lib.query('getMusicBySonger', [])
    assert load(0xf004) == 2 and lib.d[lib.show] == rows[:2]
    assert fw.calls('getMusicBySonger')[0][1][0] == 0  # the Unknown row's query: untagged songs
    assert lib.d[lib.dir] == []


def test_an_unknown_row_with_songs_stays(fw, records):
    lib = Library(fw)
    rows = [records.song(artist=b'ABBA', kind=4), records.song(id_=-1, kind=4)]
    load = Localclass(fw, lib, rows)
    lib.query('getMusicBySonger', [records.song(name=b'untagged.flac')])
    assert load(0xf004) == 2 and lib.d[lib.show] == rows and lib.d[lib.dir] == []
    assert load.timers == []


@pytest.mark.parametrize('artist_type', [0, 1])
def test_artists_follow_the_artist_pages_own_setting(fw, records, artist_type):
    lib = Library(fw)
    fw.var('fw_ctx_artist_type', C.c_int).value = artist_type
    load = Localclass(fw, lib, [records.song(id_=-1, kind=4)])
    load(0xf004)
    assert fw.var('fw_ctx_g_artist_type', C.c_int).value == artist_type
    assert len(fw.calls('getMusicByAlbumArtist' if artist_type else 'getMusicBySonger')) == 1
    assert fw.var('navigation__library_music', C.c_int).value == 0  # put back


@pytest.mark.parametrize('cls, words', [(CLASS_ALBUMS, 'No albums'), (0xf004, 'No artists'), (0xf005, 'No composers'),
                                        (0xf006, 'No genres')])
def test_an_empty_class_list_closes_its_page_once_open_and_says_so(fw, st, records, cls, words):
    ui = Ui(fw)
    lib = Library(fw)
    prompts = Prompts(fw)
    load = Localclass(fw, lib, [records.song(id_=-1, kind=4)])
    assert load(cls) == 0 and [ms for _, ms in load.timers] == [0]
    C.c_int.from_address(C.addressof(fw.var('fw_ctx_g_class_type', C.c_char))).value = cls
    page = ui.window('album_page' if cls == CLASS_ALBUMS else 'localclass_page')
    page.props['stage'] = O['WINDOW_STAGE_OPENED'] - 1
    run(load.timers[-1][0])
    assert [ms for _, ms in load.timers] == [0, O['EMPTY_POLL_MS']] and prompts.seen == []  # still opening: polled
    page.props['stage'] = O['WINDOW_STAGE_OPENED']
    run(load.timers[-1][0])
    assert [a[1] for _, a in fw.calls('window_manager_close_window_force')] == [page.addr]
    assert [p[3] for p in prompts.seen] == [words]


def test_an_empty_list_whose_page_never_showed_only_says_so(fw, st, records):
    ui = Ui(fw)
    lib = Library(fw)
    prompts = Prompts(fw)
    load = Localclass(fw, lib, [])
    load(0xf005)
    C.c_int.from_address(C.addressof(fw.var('fw_ctx_g_class_type', C.c_char))).value = 0xf005
    ui.window('localmusic_page')
    run(load.timers[-1][0])
    assert not fw.calls('window_manager_close_window_force') and [p[3] for p in prompts.seen] == ['No composers']


@pytest.mark.parametrize('page, shows', [('album_page', CLASS_ALBUMS), ('localclass_page', 0xf004)])
def test_an_empty_list_leaves_a_list_page_showing_another_class(fw, st, records, page, shows):
    ui = Ui(fw)
    lib = Library(fw)
    prompts = Prompts(fw)
    load = Localclass(fw, lib, [])
    load(0xf005)  # Composers, empty
    C.c_int.from_address(C.addressof(fw.var('fw_ctx_g_class_type', C.c_char))).value = shows
    ui.window(page).props['stage'] = O['WINDOW_STAGE_OPENED']
    run(load.timers[-1][0])
    # the page on top lists another class: it stays, unpolled, and the toast says what was empty
    assert not fw.calls('window_manager_close_window_force') and len(load.timers) == 1
    assert [p[3] for p in prompts.seen] == ['No composers']


def test_songs_load_straight_into_the_showlist(fw, records):
    lib = Library(fw)
    songs = [records.song(title=b'%d' % i, path=b'/m/%d.flac' % i) for i in range(3)]
    lib.d[lib.show][:] = [records.song(title=b'stale')]
    lib.d[lib.dir][:] = [records.song(title=b'staged')]
    C.c_int.from_address(C.addressof(fw.var('fw_ctx_g_sort_flag', C.c_char))).value = 2
    seen = []
    fw.mock('getAllMusic', lambda order, *_: seen.append((order, ctx_ptr(fw, 'tools_pdeq_directory').value)) or
            lib.staging().extend(songs) or 3)
    load = Localclass(fw, lib)
    assert load(0xf001) == 3 and lib.d[lib.show] == songs
    assert seen == [(2, lib.show)] and ctx_ptr(fw, 'tools_pdeq_directory').value == lib.dir and lib.d[lib.dir] == []
    assert not fw.calls('stock_localclass_trampoline') and fw.calls('music_nagvation_title')[0][1][0] == 0xf001


def test_a_playlist_hides_its_spoken_songs(fw, records):
    lib = Library(fw)
    fw.mock('tk_str_start_with', lambda s, p, *_: C.string_at(s).startswith(C.string_at(p)))
    rows = [records.song(title=b'song', path=b'/mnt/mmc/Music/a.flac'), records.song(title=b'book', path=b'/mnt/mmc/x.m4b'),
            records.song(title=b'episode', path=b'/mnt/mmc/Podcasts/e.mp3'), records.song(title=b'chapter', path=b'/mnt/mmc/Audiobooks/c.mp3'),
            records.song(title=b'folder', path=b'/mnt/mmc/Podcasts', kind=4)]
    load = Localclass(fw, lib, rows)
    assert load(0xff20) == 2 and load.shown() == [b'song', b'folder']


@pytest.mark.parametrize('cls', [0xf00a, 0xf008, 0xf007])
def test_stored_lists_hide_discovery_and_take_the_librarys_titles(fw, records, cls):
    lib = Library(fw)
    Files(fw, {'/mnt/data/q2-library-scope': b'0/mnt/mmc/Discovery\n'})
    played = [records.song(title=b'Kept', path=b'/mnt/mmc/Music/a.flac'),
              records.song(path=b'/mnt/mmc/Music/b.cue', cue=60),  # played from Folder: no title
              records.song(title=b'Hidden', path=b'/mnt/mmc/Discovery/c.flac'),
              records.song(path=b'/mnt/mmc/Music/d.flac')]
    library = [records.song(title=b'Track 1', path=b'/mnt/mmc/Music/b.cue', cue=0),
               records.song(title=b'Track 2', path=b'/mnt/mmc/Music/b.cue', cue=60),
               records.song(title=b'Dee', path=b'/mnt/mmc/Music/d.flac')]
    lib.query('getAllMusic', library)
    load = Localclass(fw, lib, played)
    assert load(cls) == 3 and load.shown() == [b'Kept', b'Track 2', b'Dee']
    assert [a[0] for _, a in fw.calls('getAllMusic')] == [3]  # the library once, unsorted


def test_a_cue_track_takes_its_own_title_where_its_siblings_slot_comes_first(fw, records):
    lib = Library(fw)
    path = b'/mnt/mmc/Music/b.cue'
    first, second = records.song(title=b'Track 1', path=path), records.song(title=b'Track 2', path=path)
    # setup: a CUE start for the second whose key (the C's own listen_key) lands in the first's slot, the index of
    # two songs having 4 (at most half full)
    listen_key = fw.fn('listen_key', C.c_uint, C.c_void_p)
    start, taken = C.c_int.from_address(second + O['REC_CUE_START']), listen_key(first)
    for cue in range(1, 4096):
        start.value = cue
        if listen_key(second) & 3 == taken & 3 and listen_key(second) != taken:
            break
    assert listen_key(second) & 3 == taken & 3 and listen_key(second) != taken, 'no CUE start shares the first slot'
    lib.query('getAllMusic', [first, second])
    load = Localclass(fw, lib, [records.song(path=path, cue=start.value)])
    assert load(0xf007) == 1 and load.shown() == [b'Track 2']


@pytest.mark.parametrize('row', ['folder', 'pathless song'])
def test_a_stored_list_with_nothing_to_look_up_never_reads_the_library(fw, records, row):
    lib = Library(fw)
    # every song titled, but for a folder row, or a song with no path to find it by
    other = records.song(name=b'b.flac', kind=4 if row == 'folder' else 8)
    load = Localclass(fw, lib, [records.song(title=b'A', path=b'/mnt/mmc/a.flac'), other])
    assert load(0xf00a) == 2 and not fw.calls('getAllMusic')


def test_an_untagged_song_with_no_path_reads_its_name_as_stored(fw, records):
    # track_name drops the extension the path's file name ends in; a record with no path keeps the name whole. The
    # stripping itself is test/patch.py's (iPod song rows): name, path and title sit 4 bytes apart on the Q2, which
    # no string placement here keeps readable together (Records)
    lib = Library(fw)
    load = Localclass(fw, lib, [records.song(name=b'01 Intro.flac'), records.song(title=b'Tagged'), records.song(name=b'dir', kind=4)])
    load(0xff11)
    assert load.shown() == [b'01 Intro.flac', b'Tagged', None]  # a tag stays; a folder row is left as it is


@pytest.mark.parametrize('cls, geom, shown', [(0xf004, 'GEOM_IPOD', 0), (0xf005, 'GEOM_IPOD', 1), (0xf005, 'GEOM_CLASSIC_LIGHT', 0)])
def test_a_class_rows_icon_shows_on_composers_and_genres_two_line_rows_only(fw, st, geometry, cls, geom, shown):
    names, _ = geometry
    st('geom').value = names[geom]
    ui = Ui(fw)
    row = ui.add('list_item', ui.window('localclass_page'))
    icon = ui.add('image', row, name='img_icon')
    C.c_int.from_address(C.addressof(fw.var('fw_ctx_g_class_type', C.c_char))).value = cls
    fw.mock('class_icon', lambda *_: 8)
    assert fw.fn('ringnav_class_icon', C.c_int, C.c_void_p, C.c_int)(row.addr, 2) == (8 if shown else 0)
    assert icon.visible == shown and len(fw.calls('class_icon')) == shown


# An artist's page.
class ArtistPage:
    """artistinfo_page: its navbar (hidden by iPod), tab_control with its pages and the albums grid."""
    def __init__(self, fw, ui, nav_shown=0):
        self.win = ui.window('artistinfo_page')
        self.win.props['anim_hint'] = b'htranslate'
        self.nav = ui.add('view', self.win, (0, 0, 375, 40), 'view_navbar')
        self.nav.visible = nav_shown
        self.tab = ui.add('tab_control', self.win, (0, 0, 375, 290), 'tab_control')
        self.pages = ui.add('pages', self.tab, (0, 50, 375, 240), 'pages_artistinfo')
        self.grid = ui.add('list_view', self.pages, (0, 0, 375, 240), 'list_view_album')


ARTIST_TITLE_SIZE = 0x200  # stock's title buffer, all of which artist_list's getFormatString may write


def artist(fw, stand_in, records, rows, songs, view=b'1', title=b'Queen'):
    lib = Library(fw)
    Config(fw, ARTISTCF=view)
    stand_in(O['ARTIST_TITLE'], title.ljust(ARTIST_TITLE_SIZE, b'\0'))  # the whole buffer, put back whole
    classinfo(fw, artist=b'Queen')
    fw.mock('stock_artist_trampoline', lambda tab, *_: lib.d[lib.show].extend(rows) or len(rows))
    lib.query('getMusicBySonger', songs)
    return lib


def queen(records):
    """The artist's albums tab as stock lists it, by name, and the artist's songs."""
    rows = [records.song(id_=-1, path=b'/m/x.flac', kind=4),
            records.song(album=b'Greatest Hits', artist=b'Queen', path=b'/m/Queen/GH/01.flac', kind=4),
            records.song(album=b'Covers', artist=b'Queen', path=b'/m/Tribute/Covers/01.flac', kind=4),
            records.song(album=b'A Night at the Opera', artist=b'Queen', path=b'/m/Queen/ANATO/01.flac', kind=4)]
    songs = [records.song(album=b'A Night at the Opera', path=b'/m/Queen/ANATO/0%d.flac' % i) for i in (1, 2)] + \
            [records.song(album=b'Greatest Hits', path=b'/m/Queen/GH/01.flac'), records.song(path=b'/m/x.flac')]
    return rows, songs


def test_an_artists_albums_tab_orders_its_albums_and_notes_their_ids(fw, st, stand_in, records):
    ui = Ui(fw)
    ArtistPage(fw, ui)
    rows, songs = queen(records)
    lib = artist(fw, stand_in, records, rows, songs, view=b'0')
    assert fw.fn('ringnav_artist_list', C.c_int, C.c_int)(1) == 4
    # the artist's own albums in the albums' order (no year without the database: as listed), the Unknown album
    # after them, then an album the artist has no song on
    assert lib.d[lib.show] == [rows[1], rows[3], rows[0], rows[2]]
    album_id = fw.fn('album_id', C.c_uint, C.c_void_p)
    ids = C.cast(st('art_ids', C.c_void_p).value, C.POINTER(C.c_uint))
    # each row's album_id for the hold menu's album rating; the Unknown row names no album
    assert st('art_n', C.c_uint).value == 4 and [ids[i] for i in range(4)] == [album_id(rows[1]), album_id(rows[3]), 0, album_id(rows[2])]
    assert fw.var('navigation__library_music', C.c_int).value == 0
    out = C.create_string_buffer(64)
    fw.fn('ringnav_artist_art_name', C.c_int, C.c_char_p, C.c_uint, C.c_uint, C.c_char_p)(out, 64, 1, b'Stock')
    assert out.value == b'Stock'  # no name shared: stock's own key


def test_an_artists_songs_tab_keeps_no_keys(fw, st, stand_in, records):
    ui = Ui(fw)
    ArtistPage(fw, ui)
    rows, songs = queen(records)
    artist(fw, stand_in, records, rows, songs)
    fw.fn('ringnav_artist_list', C.c_int, C.c_int)(1)
    assert st('art_n', C.c_uint).value == 4
    assert fw.fn('ringnav_artist_list', C.c_int, C.c_int)(0) == 4
    assert (st('art_n', C.c_uint).value, st('art_keys', C.c_void_p).value, st('art_ids', C.c_void_p).value) == (0, None, None)
    lib_covers = []
    fw.mock('albumcoverinfo_init', lambda list_, *_: lib_covers.append(list_) or 0)
    fw.fn('ringnav_artist_covers', C.c_int, C.c_void_p)(0x77)
    assert lib_covers == [0x77]


def test_an_artist_with_no_name_reads_unknown_artist(fw, stand_in, records):
    ui = Ui(fw)
    ArtistPage(fw, ui)
    fw.mock('getFormatString', lambda key, out, n, *_: C.memmove(out, b'Unknown Artist\0', 15) and 0)
    artist(fw, stand_in, records, [], [], title=b'(null)')
    fw.fn('ringnav_artist_list', C.c_int, C.c_int)(1)
    assert C.string_at(O['ARTIST_TITLE']) == b'Unknown Artist'
    assert [(C.string_at(a[0]), a[2]) for _, a in fw.calls('getFormatString')] == [(b'player_unknown', ARTIST_TITLE_SIZE)]


def test_grid_gives_the_artist_page_its_toolbar_back(fw, stand_in, records):
    ui = Ui(fw)
    page = ArtistPage(fw, ui)
    artist(fw, stand_in, records, [], [], view=b'0')
    params = []
    fw.mock('widget_set_self_layout_params', lambda w, *a: params.append((w, *(C.string_at(v) for v in a[:4]))))
    fw.fn('ringnav_artist_list', C.c_int, C.c_int)(1)
    assert page.nav.visible and page.tab.box == (0, 40, 375, 250)  # the tabs move down under it
    assert params == [(page.pages.addr, b'0', b'50', b'100%', b'200')]
    assert page.grid.box[3] == 200 and [a[0] for _, a in fw.calls('widget_layout')] == [page.tab.addr]


@pytest.mark.parametrize('view, shown', [(b'1', 0), (b'0', 1)])
def test_the_toolbar_stays_as_it_is_under_cover_flow_or_a_reload(fw, stand_in, records, view, shown):
    ui = Ui(fw)
    page = ArtistPage(fw, ui, nav_shown=shown)
    artist(fw, stand_in, records, [], [], view=view)
    fw.fn('ringnav_artist_list', C.c_int, C.c_int)(1)
    assert page.tab.box == (0, 0, 375, 290) and not fw.calls('widget_layout')


def test_cover_flow_view_opens_unseen_and_shows_the_page_again_if_it_cannot(fw, st, stand_in, records):
    ui = Ui(fw)
    page = ArtistPage(fw, ui)
    rows, songs = queen(records)
    artist(fw, stand_in, records, rows, songs)
    timers = []
    fw.mock('timer_add', lambda fn, ctx, ms, *_: timers.append((fn, ms)) or 3)
    fw.fn('ringnav_artist_list', C.c_int, C.c_int)(1)
    # the page goes unseen and without its slide until the covers are up
    assert page.win.props['anim_hint'] == b'' and [a[1] for _, a in fw.calls('widget_set_opacity')] == [0]
    assert st('art_cf_veil', C.c_void_p).value == page.win.addr and st('art_cf_timer').value == 3
    assert [ms for _, ms in timers] == [0]
    fw.fn('ringnav_artist_list', C.c_int, C.c_int)(1)  # a reload while it waits: once per page
    assert len(timers) == 1
    run(timers[0][0])  # no music albums for Cover Flow here: the page shows again with its slide back
    assert page.win.props['anim_hint'] == b'htranslate' and [a[1] for _, a in fw.calls('widget_set_opacity')] == [0, 0xff]
    assert not st('art_cf_win', C.c_void_p).value and not st('art_cf_veil', C.c_void_p).value
    destroy = [(fn, ctx) for w, evt, fn, ctx in ui.handlers if evt == O['EVT_DESTROY']]
    assert destroy == [(C.cast(fw.lib.navigation__artist_cf_gone, C.c_void_p).value, page.win.addr)]


def test_a_closed_artist_page_is_forgotten(fw, st):
    gone = fw.fn('navigation__artist_cf_gone', C.c_int, C.c_void_p, C.c_void_p)
    st('art_cf_win', C.c_void_p).value, st('art_cf_veil', C.c_void_p).value = 0x10, 0x20
    gone(0x30, None)
    assert (st('art_cf_win', C.c_void_p).value, st('art_cf_veil', C.c_void_p).value) == (0x10, 0x20)
    gone(0x10, None)
    gone(0x20, None)
    assert (st('art_cf_win', C.c_void_p).value, st('art_cf_veil', C.c_void_p).value) == (None, None)


def test_cover_flow_for_a_page_no_longer_on_top_drops_its_albums(fw, st, stand_in, records):
    ui = Ui(fw)
    page = ArtistPage(fw, ui)
    rows, songs = queen(records)
    artist(fw, stand_in, records, rows, songs)
    timers = []
    fw.mock('timer_add', lambda fn, ctx, ms, *_: timers.append(fn) or 3)
    fw.fn('ringnav_artist_list', C.c_int, C.c_int)(1)
    pending = st('art_cf_list', C.c_void_p).value
    ui.window('playing_page')
    destroyed = len(fw.calls('fw_deque_destroy'))
    run(timers[0])
    assert [a[0] & 0xffffffff for _, a in fw.calls('fw_deque_destroy')[destroyed:]] == [pending]
    assert page.win.props['anim_hint'] == b'htranslate'
