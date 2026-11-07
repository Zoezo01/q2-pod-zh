"""Build-time edits for the SHA-pinned UI assets (local browsing, settings, status bar) and native call sites.

No global widget hook: excluded pages stay byte-identical; iPod's list style edits are audited in place.
The audit records full original instructions and asset hashes, not search/replace patterns.
"""
import copy
import dataclasses
import functools
import json
import math
import pathlib
import re
import struct
import types
from build import CLASSIC_LIGHT_TTF, ROOT, check, fileoff, need, sha, jal

AUDIT = json.loads((pathlib.Path(__file__).resolve().parents[1]/'patch/ipod.json').read_text())
UI_ASSETS = AUDIT['assets'] | AUDIT['navbar_only'] | AUDIT['slide_only']
STOCK_ROWS = AUDIT['stock_rows']  # pages left as stock made them until a set re-pitches them (Geometry.stock_row)
# The named immediates' values (ipod.json): text rows' plain, art rows' art_.
VALUE_NAMES = list(dict.fromkeys(g['value'] for g in AUDIT['immediates'] if isinstance(g['value'], str)))
INC = (ROOT/'patch/offsets.inc').read_text()


# The integer #defines of patch/offsets.inc, which the payload compiles with.
O = {m[1]: int(m[2], 0) for m in re.finditer(r'^#define\s+(\w+)\s+(0x[0-9A-Fa-f]+|\d+)\b', INC, re.M)}
inc = O.__getitem__

@dataclasses.dataclass(frozen=True)
class Geometry:
    """The app window is the 375x320 screen minus the 30px status bar, so a 290px list holds four
    72px rows. The stock 52px artwork is drawn 1:1 (no rescaling) with an 8px inset inside the
    68px row body. Rows keep the row layout's eight-pixel left margin for the artwork.
    text_art, text_art_inset: the text pages' artwork and its inset (0 hides it, a Classic text row).
    set_row, set_top, set_rows, set_icon: settings lists hold set_rows rows of set_row from set_top, the
    icon at set_icon (0 hides it); payload rows are built 70 px high (SET_STOCK_BODY), stock's shape,
    and mapped like stock's.
    stream_row: Streaming's settings rows, which follow Row Art; taller than set_row, they have SET_ICON's icon.
    SET0 is the built-in set; a theme declares its others in its theme.xml (<geometry name>, tools/theme.py)."""
    name: str = 'ipod'
    bar: int = 30
    pitch: int = 72
    body: int = 68
    art: int = 52
    art_inset: int = 8
    art_pitch: int = 72
    art_body: int = 68
    text_art: int = 52
    text_art_inset: int = 8
    lib_pitch: int = 72  # the Library's rows, a kind of their own: its pitch, body, icon inset and icon (0 hides it)
    lib_body: int = 68
    lib_inset: int = 8
    lib_art: int = 52
    title24_y: int = 13  # text y in the body: the 24px title, the title, metadata, playlist count
    title_y: int = 10
    meta_y: int = 39
    count_y: int = 40
    art_title24_y: int = 13
    art_meta_y: int = 39
    song_pitch: int = 72  # Songs' rows, a kind of their own: its pitch, body, title and metadata y
    song_body: int = 68
    song_title24_y: int = 13
    song_meta_y: int = 39
    set_row: int = inc('SET_ROW')
    set_top: int = inc('SET_TOP')
    set_rows: int = inc('SET_ROWS')
    set_icon: int = inc('SET_ICON')
    stream_row: int = inc('SET_ROW')
    bar_px: int = 0  # the status bar's title and clock px in the Classic face (status_metrics); 0: stock's bar, no title
    bar_title_w: int = 0  # the bar title's text width (status_metrics)
    bar_right: int = 0  # Classic's bar: the right group's margin, from the nub's last column to the screen's edge
    batt_w: int = inc('BATT_BODY_W')  # the battery body under BATT_CLASSIC_LIGHT (navigation.c classic_light_battery), its nub outside
    batt_h: int = inc('BATT_BODY_H')
    text_x: int = 0  # list text's left edge, clear of the glass at the first row (0: stock's)
    action_row: int = 0  # Songs' action bar as a plain row this high (0: stock's 50 px bar)
    list_end: int = 0  # the screen row under the bar where a selected row's scroll stops: below it the glass hides its text (0: stock's end)
    home_row: int = 39  # Home's and its Settings list's row height; Python only, no C reads it
    home_lib: int = 0  # Home's Library row, in Split, slides in a list of the Library's rows (coverflow.c home_library)
    np_ipod: int = 0  # Now Playing as an iPod classic's: the art's box NP_REFLECT taller for its reflection, the times beside the bar
    # Stock's own list pages (STOCK_ROWS, Lock Screen): their rows, its title's y and box, stock's at 78. The one rule to reverse them.
    stock_row: int = 78
    stock_body: int = 70
    stock_title_y: int = 11
    stock_title_h: int = 20
    # Search's rows, stock's at 78. search_art 0 makes them two lines of text, as Songs'.
    search_row: int = 78
    search_body: int = 70
    search_art: int = 50
    search_title_y: int = 11
    search_title24_y: int = 14
    search_title_h: int = 24
    search_meta_y: int = 41
    search_meta_h: int = 16
    toolbar: int = 1  # 0: the toolbars of TOOLBAR_SWITCHES and STOCK_ROWS hide and the bar holds the title, as on NAVBAR_ONLY pages


SET0 = Geometry()
MARGIN = 8
# The 375x320 panel's glass has rounded corners that hide whatever is drawn in them. CORNER_R is
# the device calibration knob: the corner radius in pixels, fitted to device photos (V5.4I cut
# "20 of 29" at x 8, y 42 and the "Sy" of the last Home row, which needs about 80) and to stock's
# 50px status bar margins, which clear it. Text and icons near a corner keep CORNER_SLACK more.
CORNER_R = inc('GLASS_R')
CORNER_SLACK = inc('GLASS_SLACK')


def corner_inset(y):
    """The width the glass hides at each end of screen row y."""
    d = min(max(CORNER_R - y, y - (320 - CORNER_R), 0), CORNER_R)
    return math.ceil(CORNER_R - math.sqrt(CORNER_R**2 - d*d))


def corner_x(y, h):
    """The side inset, slack included, for content spanning screen rows y to y + h."""
    return max(corner_inset(y), corner_inset(y + h)) + CORNER_SLACK


# iPod: the full-screen art (bigcover_page) is the largest centred square, even so its top and bottom
# margins match, whose square corners clear the glass by CORNER_SLACK: the whole cover shows. Stock fits it
# to the 375x320 window, where a square cover is 320 high and the glass hides its corners.
BC_ART = max(s for s in range(2, 321, 2) if (375 - s) // 2 >= corner_x((320 - s) // 2, s))


# iPod status bar (system_bar.bin, 375x30). Its 16px icons sit at y 7 to 23, so both groups keep
# clear of the top corners. The play state and EQ are on the left, as in stock; Bluetooth/codec,
# Wi-Fi and the battery on the right. The clock is centred on the screen, between the wider
# group's extent with every icon shown and the same distance from the other edge; CLOCK_MIN leaves
# room for its widest text, "12:59 PM" (69px at 16px in the pinned stock font). STATUS_PAD keeps the icons
# that much further in than the corners need, so they don't look cramped against the glass.
STATUS_PAD = 2
STATUS_MARGIN = corner_x(7, 16) + STATUS_PAD
CLOCK_MIN = 105
CLOCK_TEXT = 70
# Seven full-width Home rows share one left edge clear of the rounded glass. The payload lays them out
# at runtime (coverflow_home_layout): Split narrows the list to HOME_SPLIT_W, the art right of it.


# iPod page transition: the window anim_hint of the browsing, settings and equalizer pages. Stock's
# htranslate animator slides a page in from the right and back out on Return; the duration is the
# calibration knob.
SLIDE = f'htranslate(duration={inc("PAGE_SLIDE_MS")})'
HOME_TOP = inc('HOME_TOP')
HOME_ROW = SET0.home_row
HOME_TEXT_X = inc('HOME_DOT_X') + inc('HOME_DOT') + inc('HOME_DOT_GAP')  # labels after the selection dot
HOME_LABEL_END = inc('HOME_LABEL_END')  # mirror the text inset, clear of the lower-right glass
HOME_LIST_W = 375
# iPod Now Playing (Rockbox iVideo): a 40px top row, the art band below it, then the progress bar
# with the times under its ends. Stock draws the 3x10 A-B markers at y 250; the 4px line sits at
# 253; their x follows NP_BAR through the np_bar_* immediates in ipod.json. The window starts
# at screen y Geometry.bar; the top and bottom rows take their insets from the corners. The bar
# is a capsule (round_radius half its height) and the payload rounds the art's corners (paint_cover).
# The art and the metadata keep NP_MARGIN from the sides, 12px apart; the art is as large as that
# leaves while the text column keeps NP_TEXT_W, and "3 of 12" starts in line with the art.
NP_TOP = 40
NP_ICON = 50                     # the stock 50px control icons, centred in the top row
NP_ICON_PAD = inc('NP_ICON_PAD')  # play_fav/play_order: 28px, centred, glyph ends 4px above
NP_MARGIN = 16
NP_TEXT_W = 165
NP_TITLE_PX = inc('NP_TITLE_PX')  # the title over the NP_META_PX artist and album, as Apple's hierarchy
NP_META_PX = inc('NP_META_PX')
NP_ART = 375 - 2 * NP_MARGIN - 12 - NP_TEXT_W
NP_SLIDE_H = 186                 # the swipeable art, lyrics and info pages; the dots sit below
NP_DOTS_Y = NP_SLIDE_H - 4       # above the seek bar's NP_TOUCH target
# The art's gap below to the dots matches its gap above to "3 of 12"'s 16px text.
NP_ART_Y = (NP_DOTS_Y - NP_ART - (NP_TOP - 16) // 2) // 2
NP_TOUCH = 44
NP_TIMES_Y = 265  # 14px text in a 16px label, clear of the 10px A-B markers
NP_TEXT_X = NP_MARGIN + NP_ART + 12
# Classic's Now Playing (Geometry.np_ipod), an iPod classic's: the art NP_IPOD_ART, up beside More in
# the top row, its swipeable pages NP_IPOD_LIFT higher (the lyrics, info and visualizer keep their
# place on screen, that much down them), its top down to the first row where its square top-left corner
# clears the glass by CORNER_SLACK, as the full-screen art's do; "3 of 12" under the album; the art's box NP_REFLECT
# taller, the room its reflection takes under the near edge (coverflow.c slant_render); the times in the
# Classic face, NP_IPOD_TIME_PX, beside the bar in NP_IPOD_TIME_W boxes ("-9:59:59" fits: stb scales
# ascent - descent to the size, status_metrics' model) NP_IPOD_TIME_GAP from it, NP_IPOD_TIME_H high over its NP_BAR_IPOD rows
# (half a row high of centre when one is odd), the outer edges clear of the glass over those rows. At run time the
# boxes narrow to the track's length (NP_IPOD_TIME_WS, navigation.c ipod_times), the bar widening between them.
NP_IPOD_ART = inc('NP_IPOD_ART')
NP_IPOD_LIFT = 30
NP_REFLECT = math.ceil(NP_IPOD_ART * inc('SLANT_REFLECT_PCT') / 100)
NP_IPOD_TIME_W = inc('NP_IPOD_TIME_W')
NP_IPOD_TIME_GAP = inc('NP_IPOD_TIME_GAP')
NP_IPOD_TIME_WS = [inc('NP_IPOD_TIME_W1'), inc('NP_IPOD_TIME_W2'), NP_IPOD_TIME_W]  # under 10:00, under 1:00:00, longer
NP_IPOD_TIME_TEXT = ['-0:00', '-00:00', '-0:00:00']  # each class's widest time, before the widest digit
NP_IPOD_TIME_PX = inc('NP_IPOD_TIME_PX')
NP_IPOD_TIME_H = inc('NP_IPOD_TIME_H')
NP_INFO_NAME_W = 100  # the info page's names ("Sample Rate" 76 px in the info role's 17)
NP_IPOD_TIME_Y = 255 - NP_TOUCH // 2 + (NP_TOUCH - inc('NP_BAR_IPOD')) // 2 - (NP_IPOD_TIME_H - inc('NP_BAR_IPOD') + 1) // 2  # the slider centred on 255


def with_geometry(g):
    """The row sizes and the Now Playing insets that clear the glass, for a window starting at g.bar."""
    edge = corner_x(g.bar + NP_IPOD_TIME_Y, NP_IPOD_TIME_H) if g.np_ipod else corner_x(g.bar + 251, 8)
    bar_x = max(MARGIN, edge) + g.np_ipod * (NP_IPOD_TIME_W + NP_IPOD_TIME_GAP)
    return types.SimpleNamespace(
        BOTTOM=320 - g.bar, PITCH=g.pitch, BODY=g.body, ART=g.art, ART_INSET=g.art_inset,
        NP_POS_X=max(NP_MARGIN, corner_x(g.bar + (NP_TOP - 16) // 2, 16)) + 6,  # "3 of 12", 16px text
        NP_ICONS_END=375 - corner_x(g.bar, NP_TOP),  # the 50px icon images fill the row's height
        NP_BAR_X=bar_x, NP_BAR=[bar_x, 253, 375 - 2 * bar_x, 4],
        NP_TIME_X=max(MARGIN, corner_x(g.bar + NP_TIMES_Y + 1, 14)))


GEOM_VALS = ('bar', 'set_row', 'set_icon', 'batt_w', 'batt_h', 'text_x', 'lib_art', 'list_end', 'action_row', 'stream_row', 'stock_row', 'toolbar')  # Geometry fields the payload reads at run time (themes.inc geom_vals), not immediates


def immediate_values(g=SET0):
    """{value name: word} for the named ipod.json immediates under geometry g."""
    geo = with_geometry(g)
    values = {k: v for k, v in dataclasses.asdict(g).items() if k in VALUE_NAMES}
    return values | {'scroll': geo.BOTTOM - (g.action_row or 50), 'np_bar_x': geo.NP_BAR[0], 'np_bar_w': geo.NP_BAR[2]}


IPOD = with_geometry(SET0)
BOTTOM, PITCH, BODY, ART, ART_INSET = IPOD.BOTTOM, IPOD.PITCH, IPOD.BODY, IPOD.ART, IPOD.ART_INSET
NP_POS_X, NP_ICONS_END, NP_BAR_X, NP_BAR, NP_TIME_X = IPOD.NP_POS_X, IPOD.NP_ICONS_END, IPOD.NP_BAR_X, IPOD.NP_BAR, IPOD.NP_TIME_X
check(set(vars(IPOD)) == {'BOTTOM', 'PITCH', 'BODY', 'ART', 'ART_INSET', 'NP_POS_X', 'NP_ICONS_END', 'NP_BAR_X', 'NP_BAR', 'NP_TIME_X'},
      'a with_geometry field needs its module name above')
check([NP_MARGIN, NP_ART, NP_TEXT_X, NP_TEXT_W, *NP_BAR[::2], NP_TIMES_Y, NP_TIME_X] ==  # Spotify's and Radio's
      [inc('SPOT_' + n) for n in 'ART_X ART_PX TEXT_X TEXT_W BAR_X BAR_W TIMES_Y TIME_X'.split()], 'SPOT_* != NP_*')
check(inc('NP_ART_TOP') == NP_TOP + NP_ART_Y, 'NP_ART_TOP != NP_TOP + NP_ART_Y')
NP_GREY = '#AAAAAA'              # stock secondary text (s_scrlabel_gray24l)
# The track is TRACK_COLOR; the fill is Graphite's light tone until ringnav_playing sets the
# accent's (3.3:1 or more on the track for every preset).
NP_TRACK = f'#{inc("TRACK_COLOR"):06x}'
NP_FILL = '#' + need(re.search(r'#define ACCENTS \{ 0x\w+, 0x\w+, 0x(\w+),', INC), 'Missing ACCENTS')[1]


def decode(data):
    check(data[:4] == bytes.fromhex('12122211'), 'Unexpected AWTK UI magic')
    i = 4

    def string():
        nonlocal i
        end = data.index(0, i)
        value = data[i:end].decode('utf-8')
        i = end + 1
        return value

    def node():
        nonlocal i
        check(i + 48 <= len(data), 'Truncated UI widget')
        kind = data[i:i+32].split(b'\0')[0].decode('ascii')
        i += 32
        geometry = list(struct.unpack_from('<4i', data, i))
        i += 16
        props = {}
        while data[i]:
            key, value = string(), string()
            check(key not in props, 'Duplicate UI property')
            props[key] = value
        i += 1
        children = []
        while data[i]:
            children.append(node())
        i += 1
        return [kind, geometry, props, children]

    root = node()
    check(i == len(data), 'Trailing UI data')
    return root


def walk(n):
    yield n
    for child in n[3]:
        yield from walk(child)


def encode(root):
    def node(n):
        kind, geometry, props, children = n
        return (kind.encode().ljust(32, b'\0') + struct.pack('<4i', *geometry) +
                b''.join(k.encode()+b'\0'+v.encode()+b'\0' for k, v in props.items()) +
                b'\0' + b''.join(node(c) for c in children) + b'\0')
    return bytes.fromhex('12122211') + node(root)


# Stock artist detail tabs carry literal Chinese `text` in every language; the
# stock string table already has these keys. The Albums tab and page start active (see ARTIST_ALBUMS).
ARTIST_PAGE = 'localmusic/artistinfo_page.bin'
ARTIST_TABS = {'btn_track': ('单曲', 'local_allsongs'), 'btn_album': ('专辑', 'album')}
# Stock init builds the Songs view (0x4adcbc); call the stock Albums tab click handler (0x4ac7ec)
# instead, which sets the tab state, queries the artist's albums and builds them. Songs stays a tap away.
ARTIST_ALBUMS = [(0x4aebd0, 0x2739dcbc, 0x2739c7ec), (0x4aebd4, 0x0411fc39, 0x0411f705)]


def artist_tabs(root):
    found = []
    for n in walk(root):
        name = n[2].get('name')
        if name in ARTIST_TABS:
            text, key = ARTIST_TABS[name]
            check(n[0] == 'tab_button' and n[2].get('text') == text, f'{name}: unexpected tab')
            n[2] = {('tr_text' if k == 'text' else k): (key if k == 'text' else v) for k, v in n[2].items()}
            if name == 'btn_album':
                n[2]['value'] = 'true'
            found.append(name)
        # tab_button loads before its pages sibling and can't sync it, so show the Albums view too.
        if n[0] == 'pages':
            check('value' not in n[2], 'Unexpected artist pages')
            n[2]['value'] = '1'
            found.append('pages')
    check(sorted(found) == sorted([*ARTIST_TABS, 'pages']), 'Unexpected artist tabs')


# Home: Carousel's carousel: stock's cards, plus clones of Local Music for Coverflow at index 2 and
# the Shortcut (rockbox) after Folder, each on its own icons; their
# images (patch/coverflow.c binds them) are the click targets. Stock translates label_* by name and
# ignores these, so their text is literal (the Shortcut's is set at runtime). Local Music's label is
# literal too, "Library" in every language: stock would set label_localmusic's text to small_local
# ("Local Songs"), a key only Home uses.
HOME_PAGE = 'home_page.bin'
LIBRARY = {'name': 'label_library', 'text': 'Library'}


def home_card(menu, at, row, text):
    card = copy.deepcopy(menu[3][1])
    card[2]['name'] = 'btn_' + row
    image, label = card[3]
    check(image[2].get('name') == 'img_localmusic' and label[2].get('name') == 'label_localmusic',
            'Unexpected Local Music card')
    image[2]['name'] = 'img_' + row
    label[2]['name'] = 'label_' + row
    label[2]['text'] = text
    for key, value in image[2].items():  # assets/icons/menu_<row>*.png, added to the rootfs by build.py
        if key.endswith(':bg_image'): image[2][key] = value.replace('menu_music', 'menu_' + row)
    menu[3].insert(at, card)


# Home becomes a list of the stock cards' names, in stock order with Coverflow third,
# Rockbox before Streaming and one Settings row for Playback and System settings.
# home_page_init (0x523c84) looks up no widget: its widget_foreach visitor (0x5239b4) binds img_*
# clicks and translates label_* by name, and only img_left/img_right reach the slide_menu. Each row's transparent image covers the row, on top of its label, so it takes the
# tap and is the row's click target for the wheel. The payload binds Coverflow, Rockbox (shown
# only while the card has it) and Settings, which slides list_view_homeset, hidden until then and
# holding stock's own Playback and System setting rows (labelled Playback and System), into the list's place.
HOME_ROWS = ['playing', 'localmusic', 'coverflow', 'folder', 'rockbox', 'stream', 'settings']
HOME_SETS = ['playset', 'sysset']
HOME_LITERAL = {'coverflow': 'Cover Flow', 'rockbox': 'Rockbox', 'settings': 'Settings'}
# Stock's visitor translates label_localmusic, label_playset and label_sysset by name; renamed, they keep
# these shorter literals.
HOME_RENAMED = {'localmusic': LIBRARY, 'playset': {'name': 'label_playback', 'text': 'Playback'},
                'sysset': {'name': 'label_system', 'text': 'System'}}


# The theme default list_view is a light rounded card; Home's lists clear it to transparent.
LIST_CLEAR = {f'style:{state}:{prop}': '#00000000' for state in ('normal', 'disable', 'focused')
              for prop in ('bg_color', 'border_color')}


HOME_LISTED = {'localmusic': 'img_library', 'stream': 'img_services'}  # under home_lib: renamed, so stock's name match binds them nowhere


def home_list(name, rows, g, extra=None, view_name=None):
    """A Home list_view of g.home_row rows: each a label under a full-row transparent tap image. With no rows, a
    window-high list a page's constructor fills (coverflow.c home_library, home_streaming), its view named as the page's."""
    views = []
    for i, row in enumerate(rows):
        # Long translations ellipsize and scroll inside the shared text margins.
        label = {'name': 'label_' + row, 'style': 's_scrlabel_white20l', 'only_focus': 'true', 'ellipses': 'true'}
        if row in HOME_LITERAL:
            label['text'] = HOME_LITERAL[row]
        label.update(HOME_RENAMED.get(row, {}))
        views.append(['view', [0, i * g.home_row, HOME_LIST_W, g.home_row], {'name': 'btn_' + row}, [
            ['hscroll_label', [HOME_TEXT_X, 0, HOME_LIST_W - HOME_TEXT_X - HOME_LABEL_END, g.home_row], label, []],
            ['image', [0, 0, HOME_LIST_W, g.home_row], {'name': g.home_lib and HOME_LISTED.get(row) or 'img_' + row, 'clickable': 'true'}, []]]])
    # The list_view's layout (0x5ea3a4) makes its scroll view vertical only for a mobile scroll bar,
    # which Home has none of, and scroll_view_create leaves it off; the payload navigates only
    # vertical scroll views.
    h = g.home_row * len(views) or 320 - g.bar
    view = ['scroll_view', [0, 0, HOME_LIST_W, h],
            {'name': 'scroll_view_' + (view_name or name), 'self_layout': 'default(x=0,y=0,w=100%,h=100%)', 'yslidable': 'true'}, views]
    return ['list_view', [0, HOME_TOP, HOME_LIST_W, h],
            {'name': 'list_view_' + name, 'item_height': str(g.home_row), **LIST_CLEAR, **(extra or {})}, [view]]


def ipod_home(root, g=SET0):
    geo = with_geometry(g)
    check([n[0] for n in root[3]] == ['slide_menu', 'image', 'image'], 'Unexpected home carousel')
    check([n[2]['name'] for n in root[3][0][3]] == ['btn_' + r for r in
            ('playing', 'localmusic', 'folder', 'stream', 'playset', 'sysset')], 'Unexpected home cards')
    menu = root[3][0]
    home_card(menu, 2, 'coverflow', 'Cover Flow')
    home_card(menu, 4, 'rockbox', 'Rockbox')
    menu[3][1][3][1][2].update(LIBRARY)
    # Home: Carousel's (coverflow_home_layout): the carousel and its arrows, over the lists, hidden and
    # taking no taps until chosen.
    for n in root[3]:
        n[2].update(visible='false', sensitive='false')
    # An image for the playing track's art, moved to the right panel and fitted by coverflow_home_art.
    root[3] = [['image', [0, 0, 375, geo.BOTTOM], {'name': 'img_homeart', 'draw_type': 'fill'}, []],
               home_list('home', HOME_ROWS, g), home_list('homeset', HOME_SETS, g, {'visible': 'false'}),
               *([home_list('homelib', [], g, {'visible': 'false'}, 'localmusic'),
                  home_list('homestream', [], g, {'visible': 'false'}, 'streamsset')] if g.home_lib else []), *root[3]]


def style_props(data):
    """Yield (widget, style, state, prop, value offset, value) for each property of an AWTK style file."""
    magic, _, count = struct.unpack_from('<3I', data)
    check(magic == 0xfafbfcfd, 'Unexpected AWTK style magic')
    for i in range(count):
        at, *names = struct.unpack_from('<I32s32s32s', data, 12 + 100*i)
        state, style, widget = (n.split(b'\0')[0].decode() for n in names)
        props, at = struct.unpack_from('<I', data, at)[0], at + 4
        for _ in range(props):
            _, key_len, size = struct.unpack_from('<BBH', data, at)
            prop, at = data[at+4:at+3+key_len].decode(), at + 4 + key_len
            yield widget, style, state, prop, at, data[at:at+size]
            at += size


def patch_style(data, audit):
    """Replace each edit's old value, same size, in the n states of widget/style that hold it."""
    check(sha(data) == audit['sha256'], 'Unaudited style file')
    out, props = bytearray(data), list(style_props(data))
    for widget, style, prop, old, new, n in audit['edits']:
        old, new = bytes.fromhex(old), bytes.fromhex(new)
        check(len(old) == len(new), f'{style}.{prop}: edit changes size')
        sites = [at for w, s, _, p, at, value in props if (w, s, p, value) == (widget, style, prop, old)]
        check(len(sites) == n, f'{style}.{prop}: expected {n} states with {old.hex()}, found {len(sites)}')
        for at in sites:
            out[at:at+len(new)] = new
    return bytes(out)


# systembar_showface (0x52f610) finds every widget by name, recursively from the bar,
# and each second re-shows the volume, EQ, BT, synclink and Wi-Fi widgets and resets their text and
# images, but never their geometry. So widgets iPod hides move off-screen instead of going invisible.
# The volume number stays hidden: stock already opens dialog/volume_dialog on every wheel change.
STATUS_BAR = 'system_bar.bin'
STATUS_LEFT = ['img_state', 'label_eq']
STATE_W = 18  # Classic's play state's width: its ink's last column is 282 (tools/art.py STATE_BOX)
BAR_PAD = 9  # Classic's bar: the title's and the Home play state's ink this far clear of the glass (bar_right gives the battery's)
HOME_STATE_X = 52 + BAR_PAD  # Classic's play state on Home, in the title's place: 52 the glass's reach into its ink (test_art)
STATUS_RIGHT = ['img_bt', 'img_wifi', 'label_battery', 'view_battery', 'img_battery']
STATUS_HIDDEN = ['img_vol', 'label_vol', 'img_synclink']
# The Battery setting (navigation.c bar_sync) shows one of the last three: the stock icon, stock's
# "88%" (label_battery at BATT_PCT_PX, the icons' height, and right-aligned so its width grows away
# from the corner; "100%" is BATT_PCT_W) or the payload's charge-level battery with the percentage beside it (view_battery). The layout
# skips hidden children. The Bluetooth images are 42px canvases whose ink ends at column 41: BT_REACH
# is how far the plain glyph's ink starts left of img_bt's right edge. With it, Wi-Fi and the wider
# battery, the group's ink stays CLOCK_GAP clear of the widest clock text, CLOCK_TEXT ("12:59 PM"),
# so every mode fits once a codec badge has faded; wider badges fall back to the icon (BATT_ROOM).
BATT_MODES = STATUS_RIGHT[2:4]
BATT_PCT_W, BATT_PCT_PX, BATT_ROOM, BT_REACH = (inc(n) for n in ('BATT_PCT_W', 'BATT_PCT_PX', 'BATT_ROOM', 'BT_REACH'))
BATT_H_W = inc('BATT_BODY_W') + inc('BATT_NUB_W') + inc('BATT_GAP') + BATT_PCT_W  # the payload's battery with its nub
# Classic's right group, left to right: EQ while it is on and nothing plays (never with the play state: navigation.c
# bar_state_sync), Bluetooth, Wi-Fi, the play state and the battery. On Home's own list the play state is
# img_home_state instead, in the title's place, "Q2 Pod" hidden.
STATUS_RIGHT_CLASSIC_LIGHT = ['label_eq', *STATUS_RIGHT[:2], 'img_state', *STATUS_RIGHT[2:]]
CLOCK_GAP = 4
CLASSIC_LIGHT_CLOCK_GAP = 2  # Classic's EQ, when the widest clock shows with it
CLASSIC_LIGHT_EQ_W = 25  # Classic's EQ box: its 21 px of ink in the bar's face (test_ipod), narrower than stock's 28 so the group fits at bar_right
CLASSIC_LIGHT_FONT_SRC = ROOT/'assets/fonts/texgyreheros-bold.otf'  # the Classic face's source, subset to CLASSIC_LIGHT_TTF
CLASSIC_LIGHT_FONT_NAME = pathlib.Path(CLASSIC_LIGHT_TTF).stem


def status_metrics(g, font):
    """Classic's bar text boxes. Caps of g.bar_px text sit centred on the rows above the bar's two lines, the
    fractional top row counted: the title's first ink clears the glass there. Stock's hscroll_label keeps
    2 px each side of its box, so the title's box is that much wider than its g.bar_title_w of text; the
    clock is centred, clock_text ("12:59 PM") its widest. Model:
    stb scales a font so that ascent - descent is its size, and AWTK centres the line box in the label."""
    from fontTools.ttLib import TTFont
    f = TTFont(font, lazy=True)
    asc, desc, cmap = f['hhea'].ascent, f['hhea'].descent, f.getBestCmap()
    scale = g.bar_px / (asc - desc)
    clock_text = math.ceil(sum(f['hmtx'][cmap[ord(c)]][0] for c in '12:59 PM') * scale)
    cap, h = math.ceil(f['OS/2'].sCapHeight * scale), g.bar - 1
    cap_top = (g.bar - 2 - cap) // 2
    label_y = cap_top - ((h - g.bar_px) // 2 + round(asc * scale) - cap)
    inset = corner_inset(cap_top) + BAR_PAD
    pad = 2  # the hscroll_label's margin each side
    return {'px': g.bar_px, 'cap': cap, 'cap_top': cap_top, 'label_y': label_y, 'inset': inset, 'clock_text': clock_text,
            'title': [inset - pad, label_y, g.bar_title_w + 2 * pad, h],
            'clock': [(375 - CLOCK_MIN) // 2, label_y, CLOCK_MIN, h]}


def time_widths(font, px=NP_IPOD_TIME_PX):
    """Classic's time boxes beside the bar, one a track length class (NP_IPOD_TIME_TEXT), in status_metrics'
    model: the remaining time's longest text, every digit the face's widest. The elapsed time is never wider."""
    from fontTools.ttLib import TTFont
    f = TTFont(font, lazy=True)
    cmap = f.getBestCmap()
    adv = lambda c: f['hmtx'][cmap[ord(c)]][0]
    wide = max('0123456789', key=adv)
    return [math.ceil(sum(adv(c) for c in t.replace('0', wide)) * px / (f['hhea'].ascent - f['hhea'].descent))
            for t in NP_IPOD_TIME_TEXT]


def status_bar(root, g=SET0):
    left, right = root[3]
    root[2]['style:normal:bg_color'] = '#000000'
    # Clear, not black: AWTK draws the border over the bar's fill, and Light Mode flips black to paper.
    root[2].update({f'style:{s}:border_color': '#00000000' for s in ('normal', 'disable', 'focused')})
    check([left[2].get('name'), right[2].get('name')] == ['view_left', 'view_right'], 'Unexpected status bar')
    check(root[1][3] == left[1][3] == right[1][3] == 30, 'Unexpected status bar height')
    root[1][3] = left[1][3] = right[1][3] = g.bar
    widgets = {n[2]['name']: n for n in left[3] + right[3]}
    check(sorted([*widgets, 'view_battery']) == sorted(STATUS_LEFT + STATUS_RIGHT + STATUS_HIDDEN), 'Unexpected status bar widgets')
    widgets['img_wifi'][1][2] = 14
    eq = widgets['label_eq']
    eq[1][2] = 28
    for s in ('normal', 'disable', 'focused'):
        eq[2][f'style:{s}:font_size'] = '16'
    pct = widgets['label_battery']
    pct[1][2] = BATT_PCT_W
    for s in ('normal', 'disable', 'focused'):
        check(pct[2][f'style:{s}:font_size'] == '18', 'Unexpected battery label')
        pct[2].update({f'style:{s}:font_size': str(BATT_PCT_PX), f'style:{s}:text_align_h': 'right'})
    pct[2]['visible'] = 'false'
    widgets['view_battery'] = ['view', [0, 0, BATT_H_W, 0], {'name': 'view_battery', 'visible': 'false'}, []]
    # Classic's set (bar_px): the title on the left in the Classic face, the clock centred, the icons right of it
    m = g.bar_px and status_metrics(g, CLASSIC_LIGHT_FONT_SRC)
    left[3] = [] if m else [widgets[n] for n in STATUS_LEFT]
    right[3] = [widgets[n] for n in (STATUS_RIGHT_CLASSIC_LIGHT if m else STATUS_RIGHT)]
    xm = g.bar_right if m else STATUS_MARGIN
    for view, x in ((left, m['inset'] if m else STATUS_MARGIN), (right, xm)):
        layout = view[2]['children_layout']
        check('xm=50,s=5)' in layout, 'Unexpected status bar layout')
        view[2]['children_layout'] = layout.replace('xm=50,s=5', f'xm={x},s={inc("STATUS_GAP")}')
    room = int(375 - xm - (375 / 2 + (m['clock_text'] if m else CLOCK_TEXT) / 2 + (CLASSIC_LIGHT_CLOCK_GAP if m else CLOCK_GAP)))
    if m:  # every icon, the battery with the column of soft edge left of it, and EQ 2 px short of Bluetooth's ink
        gap, eq[1][2] = inc('STATUS_GAP'), CLASSIC_LIGHT_EQ_W
        reach = BT_REACH + 2 * gap + widgets['img_wifi'][1][2] + 1 + g.batt_w + inc('BATT_NUB_W') + 2 + eq[1][2]
        check(reach <= room, f'Status bar battery needs {reach}px of {room}px')
        # bar_sync's gate reads BATT_ROOM, the built-in bar's, past this room: a theme on this bar offers only the modes
        # the gate exempts (test_theme)
        for name in STATUS_HIDDEN:
            box = widgets[name][1]
            box[0], box[3] = -200, g.bar  # still updated by stock, drawn off-screen
            root[3].append(widgets[name])
        face = {f'style:{s}:{k}': v for s in ('normal', 'disable', 'focused')
                for k, v in (('font_size', str(g.bar_px)), ('font_name', CLASSIC_LIGHT_FONT_NAME))}
        # EQ floats at the room's left edge, not left of img_bt's whole canvas, whose last BT_REACH px alone hold ink:
        # there it would reach the clock's text.
        # ponytail: it stays here with Bluetooth or Wi-Fi off, and a codec badge's ink can reach it; a badge needs
        # a track, and a track hides EQ. Move it with the group if a stopped player ever shows a badge.
        eq[1][:] = [375 - xm - room - right[1][0], m['title'][1], eq[1][2], m['title'][3]]
        eq[2].update({'floating': 'true', **face})
        # The play state is in the row: Wi-Fi's ink ends on column 261 with it, and it closes up without it.
        widgets['img_state'][1][2] = STATE_W
        # Stock's 16 px icon canvases, centred, put Bluetooth's ink (a row into its canvas) on the battery's top row
        right[1][3] = 2 * ((g.bar - 2 - g.batt_h) // 2 - 1) + 16
        left[1][3] = widgets['img_state'][1][3] = right[1][3]  # the state's 22 px art, its ink centred on the caps
        home = copy.deepcopy(widgets['img_state'])
        home[1][:] = [HOME_STATE_X, right[1][1], STATE_W, right[1][3]]
        home[2].update(name='img_home_state', visible='false')
        root[3].append(home)
        # The payload writes the top window's title (bar_title_sync) and the local time (ringnav_paint_bg).
        for name, style in (('title', 's_scrlabel_white20l'), ('clock', 's_scrlabel_white20c')):
            root[3].append(['hscroll_label', m[name], {'name': 'label_' + name, 'style': style, 'only_focus': 'true',
                                                       'ellipses': 'true', **face}, []])
        return
    shown = [[n for n in v[3] if n[2]['name'] not in BATT_MODES] for v in (left, right)]
    extent = max(STATUS_MARGIN + sum(n[1][2] for n in v) + inc('STATUS_GAP') * (len(v) - 1) for v in shown)
    width = 375 - 2 * extent
    check(width >= CLOCK_MIN and right[1][0] + right[1][2] == 375, f'Status bar clock {width}px, too narrow')
    reach = BT_REACH + 2 * inc('STATUS_GAP') + widgets['img_wifi'][1][2] + max(BATT_PCT_W, BATT_H_W, inc('BATT_INSIDE_W') + inc('BATT_NUB_W'))
    check(BATT_ROOM == room and reach <= room, f'Status bar battery needs {reach}px of {room}px')
    for name in STATUS_HIDDEN:
        box = widgets[name][1]
        box[0], box[3] = -200, g.bar  # still updated by stock, drawn off-screen
        root[3].append(widgets[name])
    root[3].append(['label', [STATUS_MARGIN, 0, 28, g.bar], {
        'name': 'label_idle', 'text': 'Q2', 'visible': 'false',
        'style:normal:font_size': '18', 'style:normal:text_color': '#FFFFFF',
        'style:normal:text_align_h': 'left'}, []])
    # The payload writes the local time here (ringnav_paint_bg).
    root[3].append(['hscroll_label', [extent, 0, width, g.bar], {
        'name': 'label_clock', 'style': 's_scrlabel_white20c', 'style:normal:font_size': '16', 'only_focus': 'true', 'ellipses': 'true'}, []])


# Stock finds every Now Playing widget by name, recursively, so they can move: title, artist
# and a new album label join the art on the slide_view's first page, so a swipe still swaps all of it for
# the lyrics or info page. The info page keeps its stock 225px column, centred; the lyrics page is the
# payload's (patch/lyrics.c). The payload fills the label_ipod_* labels (ringnav_playing); label_playlen,
# the total, hides.
PLAYING_PAGE = 'playing_page.bin'


def plain_slider(props, track, fill, bar):
    """A slider's props drawn in plain colour (stock slider paint uses bg/fg_color without images). The
    style has no theme entry, so no thumb icon either: stock then fills exactly to the value, and
    slide_with_bar keeps tap and drag."""
    props = {k: v for k, v in props.items() if not k.endswith((':bg_image', ':fg_image', ':icon', ':y_offset'))}
    for key in props:
        if key.endswith(':bg_color'): props[key] = track
        if key.endswith(':fg_color'): props[key] = fill
    props.update(style='s_ipod_progress', bar_size=str(bar))
    return props


def np_slider(slider, x, cy, w, h):
    """Now Playing's progress bar: an h-high plain track centred on row cy in an NP_TOUCH-high slider,
    the accent's fill and the Theme's height set at runtime (navigation.c np_fill, np_theme)."""
    slider[1] = [x, cy - NP_TOUCH // 2, w, NP_TOUCH]
    slider[2] = plain_slider(slider[2], NP_TRACK, NP_FILL, h)
    # No thumb: stock skips the dragger only when border_color is clear, else a touch shows its blue.
    for state in ('normal', 'pressed', 'over', 'disable', 'focused'):
        slider[2][f'style:{state}:border_color'] = '#00000000'
    # Stock draws small radii square; the four-pixel line deliberately has square ends.
    for key in slider[2]:
        if key.endswith(':round_radius'): slider[2][key] = str(h // 2)  # a capsule, track and fill


def playing_page(root, g=SET0):
    geo = with_geometry(g)
    named = {n[2].get('name'): n for n in walk(root)}
    check([n[2].get('name') for n in root[3]] == [
        'view_buttons', 'scrlabel_title', 'scrlabel_artist', 'label_playtime', 'label_playlen',
        'slide_view_view', 'slider_play', 'img_repeata', 'img_repeatb', 'image_wait'], 'Unexpected Now Playing page')
    buttons, title, artist = root[3][:3]
    buttons[1] = [0, 0, 375, NP_TOP]
    named['img_return'][1][0] = -200  # the hardware Return, as on the pages whose navbars are hidden
    for name in ('img_fav', 'img_more', 'img_playmode'):
        n = named[name]
        n[2] = {k: v for k, v in n[2].items() if not k.endswith(('_offset', 'text_align_h'))}
        if 'image' in n[2]:
            n[2]['draw_type'] = 'center'
    named['img_more'][1] = [geo.NP_ICONS_END - NP_ICON, 0, NP_ICON, NP_TOP]
    pos: list = ['label', [geo.NP_POS_X, 0, geo.NP_ICONS_END - NP_ICON - geo.NP_POS_X, NP_TOP], {
        'name': 'label_ipod_pos', 'style:normal:font_size': '16', 'style:normal:text_color': NP_GREY,
        'style:normal:text_align_h': 'left'}, []]

    lift = g.np_ipod * NP_IPOD_LIFT
    art_y, art = (NP_ART_Y, NP_ART)
    if g.np_ipod:
        art, slide_top = NP_IPOD_ART, g.bar + NP_TOP - lift
        art_y = next(y for y in range(NP_TOP) if corner_x(slide_top + y, 0) <= NP_MARGIN) + inc('NP_IPOD_ART_DROP')
    text_x = NP_MARGIN + art + 12
    text_w = 375 - NP_MARGIN - text_x
    # Favourite centres beneath More; leave 6px between its target and shuffle/order. The row ends
    # with the art, clear of the dots: the 28px glyphs, centred, end 15px above their frame's bottom.
    icons_y = art_y + art - NP_ICON + NP_ICON_PAD
    fav_x = named['img_more'][1][0]
    for name, x in (('img_playmode', fav_x - NP_ICON - 6), ('img_fav', fav_x)):
        buttons[3].remove(named[name])
        named[name][1] = [x, icons_y, NP_ICON, NP_ICON]
    top = art_y + (icons_y - NP_ICON_PAD - art_y - (28 + 6 + 22 + 6 + 22 + g.np_ipod * (6 + 20))) // 2
    if g.np_ipod:  # the text starts under More, "3 of 12" under the album
        top = max(top, lift - 2)
        pos[1] = [text_x, top + 90, text_w, 20]
    else:
        buttons[3].append(pos)
    title[1] = [text_x, top, text_w, 28]
    # every state: the labels are disabled (enable false), so a lone normal size would not apply
    title[2].update({'style': 's_scrlabel_white20l', **{f'style:{s}:font_size': str(NP_TITLE_PX) for s in ('normal', 'disable', 'focused')}})
    artist[1] = [text_x, top + 34, text_w, 22]
    for key in artist[2]:
        if key.endswith(':font_size'): artist[2][key] = str(NP_META_PX)
        if key.endswith(':text_color'): artist[2][key] = NP_GREY
        if key.endswith(':text_align_h'): artist[2][key] = 'left'
    album = copy.deepcopy(artist)
    album[1] = [text_x, top + 62, text_w, 22]
    album[2].update(name='label_ipod_album', text='')
    for key in album[2]:
        if key.endswith(':text_color'): album[2][key] = f'#{inc("NP_ALBUM_RGB"):06X}'
    for key in artist[2]:
        if key.endswith(':text_color'): artist[2][key] = f'#{inc("NP_ARTIST_RGB"):06X}'
    for n, px in ((title, NP_TITLE_PX), (artist, NP_META_PX), (album, NP_META_PX)):
        check({v for k, v in n[2].items() if k.endswith(':font_size')} == {str(px)}, 'Now Playing text keeps a state size')
    # Classic's box holds the reflection too (navigation.c paint_cover takes a box taller than wide for it)
    named['img_cover'][1] = [NP_MARGIN, art_y, art, art + g.np_ipod * NP_REFLECT]
    named['img_playstate'][1] = [NP_MARGIN + (art - 120) // 2, art_y + (art - 120) // 2, 120, 120]
    named['img_playstate'][2]['opacity'] = '0'  # retain stock gesture target without a transport glyph
    named['view_album'][3] += [title, artist, album, named['img_playmode'], named['img_fav']] + [pos] * g.np_ipod
    column = (375 - 225) // 2
    named['label_lyricmsg'][1][0] += column
    named['label_lyricmsg'][1][1] += lift
    # The payload paints each lyric line over its stock row, a blank label LYRIC_SLOT high for a line of one
    # row (patch/lyrics.c): the rows span the page, and the list layout (stock's list_view(m=0,s=0) otherwise,
    # the payload's own once it opens) starts them a slot down, so the first line can sit in the middle.
    lrc = named['view_lrc'][3][0][2]
    check(lrc['self_layout'].startswith('default(x=0,y=0,w=-10,') and 'children_layout' not in lrc, 'Unexpected lyrics scroll view')
    check(3 * inc('LYRIC_SLOT') <= 178, 'Three lyric lines must fit the page')
    lrc['self_layout'] = 'default(x=0,y=0,w=100%,h=100%)'
    lrc['children_layout'] = f'list_view(x=0,y={inc("LYRIC_SLOT")},s=0)'
    # its y set as laid out too: visualizer.c places its page by it before the first layout
    named['view_lrc'][1][1] = lift
    named['view_lrc'][2]['self_layout'] = f'default(x=0,y={lift},w=375,h=178)'
    info = named['view_info'][3]
    if g.np_ipod:  # Classic: the track's details as rows (navigation.c ringnav_np_info), from the top row's end to the dots:
        # stock's label the name, left, and a <name>_value hscroll_label beside it the value, right-aligned; both
        # ellipsized as a settings row is (stock's label can't be, so the name's is an hscroll_label too: stock's
        # np_info only looks it up and sets its text), in the info role's face, whose size the rows hold (test/build.py).
        row = NP_DOTS_Y // len(info)  # the page's rows from lift (the window's NP_TOP) to the dots at NP_DOTS_Y + lift
        x = max(g.text_x, *(corner_x(g.bar + NP_TOP + row * i, row) for i in range(len(info))))
        for i, n in enumerate(list(info)):
            n[1] = [x, lift + row * i, NP_INFO_NAME_W, row]
            n[0] = 'hscroll_label'
            n[2] = {k: v for k, v in n[2].items() if k not in ('line_wrap', 'word_wrap', 'style:normal:text_align_v')}
            n[2].update({'style': 's_scrlabel_white20l', 'style:normal:text_align_h': 'left', 'only_focus': 'true', 'ellipses': 'true'})
            info.append(['hscroll_label', [x + NP_INFO_NAME_W, n[1][1], 375 - 2 * x - NP_INFO_NAME_W, row], {
                'name': n[2]['name'] + '_value', 'style': 's_scrlabel_white20r', 'only_focus': 'true', 'ellipses': 'true'}, []])
    else:
        for n in info:
            n[1][0] += column
            n[1][1] += lift
    # Classic's page reaches past the dots, down to its art's reflection, so a swipe takes it along
    slide_h = max(NP_SLIDE_H + lift, art_y + art + g.np_ipod * NP_REFLECT)
    named['slide_view_view'][1] = [0, NP_TOP - lift, 375, max(NP_DOTS_Y + lift + 10, slide_h)]
    named['slide_view'][1] = [0, 0, 375, slide_h]
    dots = named['slide_indicator1']
    for key in dots[2]:
        if key.endswith(':selected_fg_color'): dots[2][key] = '#eeeeee'
        elif key.endswith(':fg_color'): dots[2][key] = '#555555'
    dots[1][1] = NP_DOTS_Y + lift
    dots[2]['self_layout'] = f'default(x=0,y={NP_DOTS_Y + lift},w=100%,h=10)'
    # Without an anchor, stock's indicator centres its dots on the slide_view as widget_to_screen places it,
    # which takes away the view's own xoffset, a swipe's drag: dots laid out mid-swipe (any relayout lays them
    # out) slid with the page until the next layout. The same point as the indicator's own anchor: the
    # slide_view's centre, and above the indicator's middle, which puts the dots on its top edge.
    dots[2].update(anchor_x=f'{375 // 2}px', anchor_y='0px')
    named['image_wait'][1] = [NP_MARGIN + (art - 54) // 2, NP_TOP - lift + art_y + (art - 54) // 2, 54, 54]

    x, y, w, h = geo.NP_BAR
    np_slider(named['slider_play'], x, y + h // 2, w, h)
    for name in ('img_repeata', 'img_repeatb'):
        named[name][1][0] = x
    total = named['label_playlen']
    remain = copy.deepcopy(total)
    remain[1] = [375 - geo.NP_TIME_X - 80, NP_TIMES_Y, 80, 16]
    remain[2].update(name='label_ipod_remain', text='')
    total[2]['visible'] = 'false'
    named['label_playtime'][1] = [geo.NP_TIME_X, NP_TIMES_Y, 80, 16]
    for n in (named['label_playtime'], remain):
        for key in n[2]:
            if key.endswith(':text_color'): n[2][key] = NP_GREY
    if g.np_ipod:  # an iPod classic's times: beside the bar, centred on its rows as stock centres them
        check(y + h // 2 == 255, 'Classic\'s times are centred on the bar at 255')
        check(time_widths(CLASSIC_LIGHT_FONT_SRC) == NP_IPOD_TIME_WS, 'NP_IPOD_TIME_W1, _W2, _W != the times\' measures')
        for n, box_x, align in ((named['label_playtime'], x - NP_IPOD_TIME_GAP - NP_IPOD_TIME_W, 'right'),
                                (remain, x + w + NP_IPOD_TIME_GAP, 'left')):
            n[1] = [box_x, NP_IPOD_TIME_Y, NP_IPOD_TIME_W, NP_IPOD_TIME_H]
            for state in [k.split(':')[1] for k in n[2] if k.endswith(':text_color')]:
                n[2].update({f'style:{state}:text_color': '#FFFFFF', f'style:{state}:font_name': CLASSIC_LIGHT_FONT_NAME,
                             f'style:{state}:font_size': str(NP_IPOD_TIME_PX), f'style:{state}:text_align_h': align})
    root[3][1:3] = []
    root[3].insert(3, remain)
    if g.np_ipod:  # the times over the art's page, whose reflection reaches their row (its frame is opaque);
        # More over the pages now in its row, the window's last child so it takes its own touches
        for n in (named['label_playtime'], remain):
            root[3].remove(n)
        at = root[3].index(named['slide_view_view']) + 1
        root[3][at:at] = [named['label_playtime'], remain]
        buttons[3].remove(named['img_more'])
        root[3].append(named['img_more'])
        # Spotify's and Internet Radio's pages take these places (navigation.c stream_ipod), in the window
        dy = NP_TOP - lift
        check([NP_MARGIN, dy + art_y, art, text_x, dy + title[1][1], fav_x, dy + icons_y, x, NP_IPOD_TIME_Y + (NP_IPOD_TIME_H - inc('NP_BAR_IPOD') + 1) // 2,
               *(n[1][1] - title[1][1] for n in (artist, album, pos))] ==
              [inc('SPOT_ART_X'), *(inc('NP_IPOD_' + k) for k in 'ART_Y ART TEXT_X TEXT_Y FAV_X FAV_Y BAR_X BAR_Y'.split()), 34, 62, 90],
              "NP_IPOD_* != Classic's Now Playing")


# Display's Centred Now Playing (IPOD NPCENTRED) opens stock's own page, its art centred over the
# title and artist, as PLAYING_STOCK (navigation.c ringnav_ui_load); the payload binds it by the same names.
# It keeps stock's layout; the title and artist hold at their start as every scrolling label does, and the lyrics
# span their column, as the iPod page's span the screen (patch/lyrics.c wraps at the rows' width).
PLAYING_STOCK = 'playing_page_stock.bin'
def playing_stock(data):
    check(sha(data) == UI_ASSETS[PLAYING_PAGE], f'{PLAYING_PAGE}: unaudited UI asset')
    root = decode(data)
    named = {n[2].get('name'): n for n in walk(root)}
    check(root[2].get('name') == 'playing_page' and named['slide_view'][1][2] == 225, 'Unexpected stock Now Playing')
    lrc = named['scroll_lrc'][2]
    check(lrc['self_layout'].startswith('default(x=0,y=0,w=-10,'), 'Unexpected lyrics scroll view')
    lrc['self_layout'] = 'default(x=0,y=0,w=100%,h=100%)'
    # Full-screen lyrics (navigation.c np_full, NP_COLUMN_*) take the column and its page from stock's place to the
    # window's width: the lyrics end 20 px above the page's foot there too (178 of 198 px), and "No lyrics" stays
    # centred across (at its stock y, so high in full screen).
    view, msg = named['view_lrc'][2], named['label_lyricmsg']
    check(named['slide_view_view'][1] == [80, 0, 225, 290] and named['slide_view'][1] == [0, 0, 225, 198] and
          view['self_layout'] == 'default(x=0,y=0,w=100%,h=178)' and msg[1] == [0, 73, 225, 28] and
          'self_layout' not in msg[2], 'Unexpected stock lyrics page')
    view['self_layout'] = 'default(x=0,y=0,w=100%,h=-20)'
    msg[2]['self_layout'] = 'default(x=0,y=73,w=100%,h=28)'
    # Stock's art meets the status bar: it starts 8 px below it, 8 px smaller so its bottom stays
    # put, with the play-state badge still centred on it.
    check(named['img_cover'][1] == [18, 0, 188, 188] and named['img_playstate'][1] == [52, 34, 120, 120],
          'Unexpected stock cover')
    named['img_cover'][1] = [22, 8, 180, 180]
    named['img_playstate'][1] = [52, 38, 120, 120]
    # The play mode mirrors the heart: its box, flipped to the right edge, the icon centred in it, not
    # drawn from the corner 12 px right of the box.
    fav, mode = named['img_fav'], named['img_playmode']
    check(fav[1] == [2, 129, 86, 73] and fav[2]['draw_type'] == 'center' and mode[2]['draw_type'] == 'default',
          'Unexpected stock heart or play mode')
    mode[1] = [375 - fav[1][0] - fav[1][2], *fav[1][1:]]
    mode[2]['draw_type'] = 'center'
    for key in [k for k in mode[2] if k.endswith(('x_offset', 'y_offset'))]: del mode[2][key]
    # The heart's ink sits high in its bitmap (weighted centre 11 px of 28, the play modes' 13-14), so
    # it drops 3 px to sit level with the play mode by eye.
    fav[1] = [fav[1][0], fav[1][1] + 3, *fav[1][2:]]
    # The progress bar is the iPod page's (np_slider), so it takes the accent and Theme, where stock's
    # images drew theirs: its 82 px box's middle, 6 px down.
    bar = named['slider_play']
    check(bar[1] == [50, 208, 290, 82] and bar[2]['style:normal:y_offset'] == '6', 'Unexpected stock progress bar')
    np_slider(bar, 50, 208 + 82 // 2 + 6, 290, NP_BAR[3])
    return encode(root)


# The full-screen art (a tap on Now Playing's opens it, any tap closes it): stock's art, shade and
# song name, the art a centred BC_ART square on the window's black (bigcover_page_init only loads its image
# and sets its draw type, scale_auto as here), the shade and the name on its foot as stock has them on the
# screen's. paint_cover rounds the playing art after scaling.
BIGCOVER = 'bigcover_page.bin'
BC_TITLE_PAD = inc('BC_TITLE_PAD')


def bigcover_page(root):
    check(root[2].get('fullscreen') == 'true' and [n[2].get('name') for n in root[3]] == [
        'img_bigcover', 'view_shade', 'scrlabel_title'], 'Unexpected full-screen art page')
    art, shade, title = root[3]
    check(art[2].get('self_layout') == 'default(x=0,y=0,w=100%,h=100%)' and art[2].get('draw_type') == 'scale_auto' and
          shade[1] == [0, 270, 375, 50] and title[1] == [42, 281, 290, 28], 'Unexpected full-screen art layout')
    x, y = (375 - BC_ART) // 2, (320 - BC_ART) // 2
    art[1] = [x, y, BC_ART, BC_ART]
    art[2]['self_layout'] = f'default(x={x},y={y},w={BC_ART},h={BC_ART})'
    foot = y + BC_ART - shade[1][3]
    title[1] = [x + BC_TITLE_PAD, foot + title[1][1] - shade[1][1], BC_ART - 2 * BC_TITLE_PAD, title[1][3]]
    shade[1] = [x, foot, BC_ART, shade[1][3]]


# Quick settings (the pull-down statusbar_dialog, which covers the whole screen): the eight
# stock 60px controls stay in their four columns, two rows from QS_TOP. Each label gets the same
# two-line QS_LABEL_H area in the stock 16px style, top-aligned so single- and two-line labels start
# on one line, QS_LABEL_GAP under its icon and QS_ROW_GAP above the next row. Brightness becomes a
# slim QS_BAR track in a QS_TOUCH-high slider (tap or drag anywhere on it, as stock) between the
# stock dim and bright suns, which keep their images. dialog_statusbar_dialog_init (0x4a0d88)
# finds these widgets by name and never moves or resizes them.
QUICK_SETTINGS = 'dialog/statusbar_dialog.bin'
QS_TOP, QS_ICON, QS_LABEL_GAP, QS_LABEL_H, QS_ROW_GAP, QS_LABEL_W = 20, 60, 6, 40, 12, 80
QS_PITCH = QS_ICON + QS_LABEL_GAP + QS_LABEL_H + QS_ROW_GAP
QS_SUN, QS_BAR, QS_TOUCH = 26, 6, 48
QS_EDGE = 30                     # the suns line up with the first and last icon columns
QS_GRID = {'wifiswitch': 'wifi', 'btswitch': 'bt', 'lock': 'lock', 'gain': 'gain',
           'usbmode': 'usbmode', 'po': 'outputway', 'playset': 'playset', 'sysset': 'sysset'}


def quick_settings(root):
    menu, light = root[3]
    check([menu[2].get('name'), light[2].get('name')] == ['view_menu', 'view_backlight'], 'Unexpected quick settings')
    named = {n[2]['name']: n for n in menu[3]}
    check(sorted(named) == sorted([f'img_{k}' for k in QS_GRID] + [f'label_{v}' for v in QS_GRID.values()]),
            'Unexpected quick settings controls')
    for i, (icon, label) in enumerate(QS_GRID.items()):
        image, text = named['img_' + icon], named['label_' + label]
        x, y, w, h = image[1]
        check((w, h) == (QS_ICON, QS_ICON) and y == (0, 112)[i // 4] and text[2].get('style') == 's_label_white18c',
                f'{icon}: unexpected quick settings control')
        image[1][1] = (i // 4) * QS_PITCH
        text[1] = [x + (QS_ICON - QS_LABEL_W) // 2, image[1][1] + QS_ICON + QS_LABEL_GAP, QS_LABEL_W, QS_LABEL_H]
        text[2].update({'style': 's_label_white16c', 'style:normal:text_align_v': 'top'})
    menu[1] = [0, QS_TOP, 375, 2 * QS_PITCH - QS_ROW_GAP]
    slider, dim, bright = light[3]
    check([n[2]['name'] for n in light[3]] == ['slider_backlight', 'image0', 'image1'] and
            (dim[1][2], bright[1][2]) == (QS_SUN, QS_SUN), 'Unexpected brightness row')
    light[1] = [0, menu[1][1] + menu[1][3] + QS_ROW_GAP, 375, QS_TOUCH]
    check(light[1][1::2] == [inc('VOL_PANEL_Y'), inc('VOL_PANEL_H')], 'The volume panel must cover the brightness row')
    dim[1] = [QS_EDGE, (QS_TOUCH - QS_SUN) // 2, QS_SUN, QS_SUN]
    bright[1] = [375 - QS_EDGE - QS_SUN, (QS_TOUCH - QS_SUN) // 2, QS_SUN, QS_SUN]
    track = [QS_EDGE + QS_SUN + 12, 0, 375 - 2 * (QS_EDGE + QS_SUN + 12), QS_TOUCH]
    check(slider[2].get('slide_with_bar') == 'true', 'Unexpected brightness slider')
    props = plain_slider(slider[2], NP_TRACK, NP_FILL, QS_BAR)  # Now Playing's; the accent at runtime (quick_fill)
    props.update(self_layout='default(x={},y=0,w={},h={})'.format(*track[::2], QS_TOUCH), dragger_size=str(QS_TOUCH))
    slider[1], slider[2] = track, props


# The confirm pair (img_cancel, img_enter; 80px tiles around 60px discs) becomes two
# full-width text rows, Cancel and OK (after maks112v/qpod).
CONFIRM = 'dialog/confirminfo_dialog.bin'
CONFIRM_TILE = 80
CONFIRM_TOP, CONFIRM_ROW = inc('CONFIRM_TOP'), 48  # Cancel and OK, under the prompt


def confirm_dialog(root):
    check([n[2].get('name') for n in root[3]] == ['img_cancel', 'img_enter'] and
            all(n[1][2:] == [CONFIRM_TILE, CONFIRM_TILE] for n in root[3]), 'Unexpected confirm dialog')
    # Clear, as the status bar's: the dialog's border is drawn over the bar's ends, paper in Light Mode.
    root[2].update({f'style:{s}:border_color': '#00000000' for s in ('normal', 'disable', 'focused')})
    # Two full-width text rows under the prompt instead of the disc tiles, so the wheel's selection
    # is the lists' own bar or card. Stock looks both up by name and binds the same callbacks.
    for i, n in enumerate(root[3]):
        n[0], n[1] = 'button', [0, CONFIRM_TOP + i * CONFIRM_ROW, 375, CONFIRM_ROW]
        n[2] = {'name': n[2]['name'], 'text': ('Cancel', 'OK')[i], 'focusable': 'false',
                'style:normal:font_size': '20', 'style:normal:text_align_h': 'center'}
        # Every state, or AWTK's button/default shows its blue border (1px) and fill; a tap gets
        # the lists' pressed grey, the wheel the selection.
        for state in ('normal', 'pressed', 'over', 'focused'):
            n[2].update({'style:%s:bg_color' % state: '#333333' if state == 'pressed' else '#00000000',
                         'style:%s:border_color' % state: '#00000000', 'style:%s:border_width' % state: '0',
                         'style:%s:text_color' % state: '#FFFFFF'})


# The volume dialog loses its highlight="default(alpha=200)", so the window manager
# creates no highlighter and nothing under it dims; the payload draws the volume into the dialog
# (navigation.c vol_paint): a band on Now Playing, a panel elsewhere.
VOLUME = 'dialog/volume_dialog.bin'


def volume_dialog(root):
    check(root[0] == 'dialog' and root[2].get('name') == 'volume_dialog' and
            root[2].get('highlight') == 'default(alpha=200)', 'Unexpected volume dialog')
    del root[2]['highlight']
    # Snapshot paints can precede vol_paint; never expose the stock full-screen controls.
    check([(n[0], n[2].get('name')) for n in root[3]] ==
          [('slider', 'slider_vol'), ('label', 'label_vol')], 'Unexpected volume controls')
    for child in root[3]:
        child[2]['visible'] = 'false'


# Settings and Streaming lose their navbar, as on the local pages, and their lists hold
# SET_ROWS complete SET_ROW rows from SET_TOP; ipod_list_layout (patch/navigation.c) lays the native
# rows out to match. Tidal keeps its navbars: most hold a search button with no hardware equivalent.
NAVBAR_ONLY = AUDIT['navbar_only']
SET_ROW, SET_TOP, SET_ROWS, SET_STOCK_ROW, SET_STOCK_BODY = (inc(n) for n in ('SET_ROW', 'SET_TOP', 'SET_ROWS', 'SET_STOCK_ROW', 'SET_STOCK_BODY'))

# The re-pitched list pages' rows: art pages' carry artwork (Albums, Folder), the Library's are its own
# (lib_*), text pages' carry none. Any other page rows() re-pitches (the playlist actions) is text. test/build.py imports it.
PAGE_KIND = {'localmusic/allmusic_page.bin': 'song', 'localmusic/albuminfo_page.bin': 'text',
             'localmusic/artistinfo_page.bin': 'text', 'localmusic/playlist_page.bin': 'playlist',
             'localmusic/album_page.bin': 'art', 'folder_page.bin': 'art', 'localmusic_page.bin': 'lib',
             'localmusic/localclass_page.bin': 'text'}


def page_rows(g, kind):
    """(pitch, body, artwork inset) of a page kind's rows under g."""
    return {'art': (g.art_pitch, g.art_body, g.art_inset), 'lib': (g.lib_pitch, g.lib_body, g.lib_inset),
            'song': (g.song_pitch, g.song_body, g.text_art_inset),
            # a set without toolbars gives Playlists two-line rows; navigation.c's pl_dress sizes them
            'playlist': (g.pitch, g.body, g.text_art_inset) if g.toolbar else (g.song_pitch, g.song_body, g.text_art_inset)
            }.get(kind, (g.pitch, g.body, g.text_art_inset))


def text_y(g, y):
    """A stock row label's y in the 70px body in g's body: the 20px title (11), the 24px title (14),
    a 28px label (21), metadata (40) and a count (41)."""
    return {11: g.title_y, 14: g.title24_y, 21: (g.body - 28) // 2, 40: g.meta_y, 41: g.count_y}[y]


# The settings rows' stock 52px artwork (SET_STOCK_ICON), pinned by hash in ipod.json,
# is pre-sized to SET_ICON at build time so the rows draw it 1:1 instead of scaling it on the device.
# ImageMagick's Lanczos resize weights colour by alpha, so edges keep their colour and transparency;
# -strip and the excluded date chunks keep the bytes reproducible. Only native settings code names
# these images, so no other screen sees the smaller size. They keep their colours for Classic; Minimal
# greys them as they load (navigation.c ringnav_image_add), as it does list_into's ink.
SETTINGS_ICONS = AUDIT['settings_icons']
SET_ICON, SET_STOCK_ICON = inc('SET_ICON'), inc('SET_STOCK_ICON')


def png_header(data):
    """(width, height, bit depth, colour type) from a PNG's IHDR."""
    check(data[:8] == b'\x89PNG\r\n\x1a\n' and data[12:16] == b'IHDR', 'Not a PNG')
    return struct.unpack('>IIBB', data[16:26])


def imagemagick(*args, data):
    import shutil, subprocess
    tool = need(shutil.which('magick') or shutil.which('convert'), 'ImageMagick (magick or convert) is required for the iPod settings icons')
    return subprocess.run([tool, *args], input=data, stdout=subprocess.PIPE, check=True).stdout


# Output PNGs: no metadata, always 8-bit RGBA.
PNG_OUT = ['-strip', '-define', 'png:exclude-chunks=date,time', '-define', 'png:color-type=6',
           '-define', 'png:bit-depth=8', 'png:-']

STATUS_ICONS = AUDIT['status_icons']


def status_icon(name, data):
    """One optical weight, rendered at 4x for smooth edges on the native-size panel."""
    check(sha(data) == STATUS_ICONS.get(name), f'{name}: unaudited status icon')
    width = 14 if name.startswith('bar_wifi') else 42 if name.startswith('bar_bt') else 12
    if name.startswith('bar_wifi'):
        level = int(name[-5])
        # Concentric arcs (+-44 degrees) round the dot at 7,12.6: ink rows 3-14, the battery's 11 px.
        paths = ('M0.88,6.27 A8.8,8.8 0 0,1 13.12,6.27', 'M2.76,8.21 A6.1,6.1 0 0,1 11.24,8.21',
                 'M4.64,10.15 A3.4,3.4 0 0,1 9.36,10.15')
        shapes = 'stroke-width 1.5 ' + ' '.join(f"stroke '{'white' if level >= 4-i else '#666666'}' path '{p}'"
                                                for i, p in enumerate(paths))
        shapes += f" stroke none fill '{'white' if level else '#666666'}' circle 7,12.6 8.2,12.6"
    elif name.startswith('bar_bt'):
        color = 'white' if name == 'bar_btcon.png' else '#666666'
        shapes = f"stroke '{color}' path 'M34.2,4 L41.2,11 L37.2,14 L37.2,2 L41.2,5 L34.2,12'"
    elif name == 'bar_play.png':
        shapes = "fill white stroke white stroke-width 1 path 'M2,2.5 L10,8 L2,13.5 Z'"
    else:
        shapes = "stroke white stroke-width 2.5 path 'M3,3 L3,13 M9,3 L9,13'"
    draw = 'scale 4,4 fill none stroke-width 1.6 stroke-linecap round stroke-linejoin round '+shapes
    return imagemagick('-size', f'{width*4}x64', 'xc:none', '-draw', draw,
                      '-filter', 'Lanczos', '-resize', f'{width}x16!', *PNG_OUT, data=b'')



def row_icon(data, size=SET_ICON, sharp=False):
    """A 52px row icon pre-sized to SET_ICON, or the pane's size, so it draws 1:1 (set_child) instead of
    scaling it on the device with hard edges. sharp: a light unsharp mask after the shrink, so the
    Streaming logos stay as crisp as list_tidal beside them (the audited settings icons keep stock's
    plain filter, test/build.py compares their colours)."""
    return imagemagick('png:-', '-alpha', 'on', '-filter', 'Lanczos', '-resize', f'{size}x{size}!',
                       *(['-unsharp', '0x0.6+0.8+0'] if sharp else []), *PNG_OUT, data=data)


# Each stock placeholder's replacement size: stock's own, but Now Playing's and the full-screen art's at
# the art they fill (stock's 188 scaled down there on the device, jagged), and the album grids' at
# album_children's inset cover (110 less TILE_INSET + TILE_CLEARANCE a side), drawn 1:1 by its scale-down.
# The square ones keep the disc's transparent corners, so a selected row's bar shows through them; under
# square art (ipod_square_art) navigation.c's square_disc fills them as they load.
ART_PLACEHOLDERS = {'default_album_big.png': 110 - 2 * (inc('TILE_INSET') + inc('TILE_CLEARANCE')), 'default_album_small.png': 52,
                    'play_defaultcover.png': NP_ART, 'scrsaver_playcover.png': 60,
                    'default_smallcover.png': 140, 'default_bigcover.png': BC_ART}


def sized_art(data, size):
    w, h = size if isinstance(size, tuple) else (size, size)
    px = min(w, h)
    return imagemagick('png:-', '-filter', 'Lanczos', '-resize', f'{px}x{px}!', '-background', 'black' if w != h else 'none',
                       '-gravity', 'center', '-extent', f'{w}x{h}', *PNG_OUT, data=data)


def settings_icon(name, data):
    check(sha(data) == SETTINGS_ICONS.get(name), f'{name}: unaudited settings icon')
    check(png_header(data) == (SET_STOCK_ICON, SET_STOCK_ICON, 8, 6), f'{name}: unexpected stock icon format')
    out = row_icon(data)
    check(png_header(out) == (SET_ICON, SET_ICON, 8, 6), f'{name}: filtered icon is not {SET_ICON}px RGBA')
    opaque = [imagemagick('png:-', '-format', '%[opaque]', 'info:', data=d) for d in (data, out)]
    check(opaque[0] == opaque[1], f'{name}: filtering changed the transparency')
    return out


QUIET_ICONS = AUDIT['quiet_icons']
SETTINGS_SLIDERS = ('systemset/backlight_page.bin', 'playset/balance_page.bin',
                    'playset/maxvol_page.bin', 'playset/bootvol_page.bin')
# Pages whose init puts an on/off switch in the toolbar (img_bootswitch, img_switch); they keep it.
TOOLBAR_SWITCHES = ('systemset/sleepshutdown_page.bin', 'playset/bootvol_page.bin', 'systemset/screensaver_page.bin')
# Pages that keep their toolbar under any set: the slider page's is Crossfade's and its own; these two hold buttons or have no switch to move.
KEEP_TOOLBAR = ('playset/bootvol_page.bin', 'playerjumpinfo_page.bin', 'specfolder_page.bin')
SLEEP_TIMER, LOCK_SCREEN = 'systemset/sleepshutdown_page.bin', 'systemset/screensaver_page.bin'


def slider_plain(path, root):
    """A settings slider loses its endpoint glyphs: the bar, value and -/+ say enough. The images stay,
    named, imageless and empty, as stock looks them up."""
    if path not in SETTINGS_SLIDERS:
        return
    slider, = [n for n in walk(root) if n[0] == 'slider']
    slider[2] = plain_slider(slider[2], NP_TRACK, NP_FILL, 8)
    for state in ('normal', 'pressed', 'over', 'disable', 'focused'):
        slider[2].update({f'style:{state}:round_radius': '4',
                          f'style:{state}:border_color': '#00000000'})
    endpoints = [n for n in walk(root) if n[0] == 'image' and n[2].get('image') in (
        'drop_lighleft', 'drop_lightright', 'balance_left', 'balance_right', 'vol_left', 'vol_right')]
    check(len(endpoints) == 2, f'{path}: unexpected slider endpoints')
    for n in endpoints:
        del n[2]['image']
        n[1][2:] = [0, 0]


def quiet_icon(name, data):
    check(sha(data) == QUIET_ICONS.get(name), f'{name}: unaudited control icon')
    return imagemagick('png:-', '-trim', '+repage', '-filter', 'Lanczos', '-resize', '24x24',
                      '-channel', 'RGB', '-fill', '#aaaaaa' if name == 'play_moredown.png' else '#eeeeee',
                      '-colorize', '100', '+channel', '-gravity', 'center', '-background', 'none',
                      '-extent', '28x28', *PNG_OUT, data=data)


def branch(op, at, target):
    """op (beq, b, bal...) at address at, to target."""
    delta = (target - at - 4) // 4
    check(-32768 <= delta <= 32767, f'{at:#x}: branch out of range')
    return op | (delta & 0xffff)


def patch_word(data, address, old, new, purpose, changes=None):
    off = fileoff(data, address)
    check(struct.unpack_from('<I', data, off)[0] == old, f'{address:#x}: unexpected instruction')
    struct.pack_into('<I', data, off, new)
    if changes is not None:
        changes.append(dict(address=hex(address), original=hex(old), patched=hex(new), purpose=purpose))


def songs_action_row(root, g):
    """Songs' action bar as a plain row: the row and what is under it. Stock rebuilds the row's children on each refresh, so the
    payload shapes those as it paints (navigation.c action_row)."""
    row = next(n for n in root[3] if n[2].get('name') == 'view_navbar_allplay')
    row[1][3] = g.action_row
    for n in root[3]:
        if n[1][1] == 50 and n is not row:
            if n[0] == 'table_view':
                n[1][3] = 320 - g.bar - g.action_row
            n[1][1] = g.action_row


def patch_asset(path, data, g=SET0):
    geo = with_geometry(g)
    pitch, body, inset = page_rows(g, PAGE_KIND.get(path, 'text'))
    stock = path in STOCK_ROWS
    check(sha(data) == (STOCK_ROWS if stock else UI_ASSETS)[path], f'{path}: unaudited UI asset')
    if stock and g.toolbar and g.stock_row == SET0.stock_row:
        return data
    if stock:
        pitch, body = g.stock_row, g.stock_body
    plain = not g.toolbar and path not in KEEP_TOOLBAR  # the bar holds the title, a switch moves to a row (navigation.c switch_row)
    fits = g.stock_row == g.set_row  # at any other pitch stock's rows aren't settings rows: ipod_list_layout
    settings = (path in NAVBAR_ONLY and (fits or not plain or path != LOCK_SCREEN)) or (stock and plain and fits and path != 'playerqueue_page.bin')
    root = decode(data)
    check(encode(root) == data, f'{path}: UI round trip differs')
    if path == ARTIST_PAGE:
        artist_tabs(root)
    whole: dict = {HOME_PAGE: functools.partial(ipod_home, g=g), STATUS_BAR: functools.partial(status_bar, g=g),
             PLAYING_PAGE: functools.partial(playing_page, g=g), QUICK_SETTINGS: quick_settings,
             CONFIRM: confirm_dialog, VOLUME: volume_dialog, BIGCOVER: bigcover_page}
    if path in whole:
        whole[path](root)
        return encode(root)
    if path == 'dialog/addplaylist_dialog.bin':
        edit = root[3][0][3][0]
        check(edit[0] == 'edit' and edit[2].get('name') == 'ssedit' and
              edit[2].get('sensitive') == 'false', 'Unexpected new playlist editor')
        edit[2]['sensitive'] = 'true'
        return encode(root)
    # Home is never reopened; Now Playing, Coverflow and the dialogs open at once.
    check(root[0] == 'window' and not any(k.endswith('anim_hint') for k in root[2]), f'{path}: unexpected window')
    if not stock:
        root[2]['anim_hint'] = SLIDE
    if path in AUDIT['slide_only']:  # the PEQ editor builds its own children (patch/peq_ui.c)
        return encode(root)
    nav = [n for n in root[3] if n[2].get('name') == 'view_navbar']
    check(len(nav) == 1 and nav[0][1] in ([0, 0, 375, 50], [0, 0, 370, 50]), f'{path}: unexpected toolbar')  # 370: stream_page
    # Keep the widget (and callback lookups) alive. Children may be recreated by stock.
    playlist = path == 'localmusic/playlist_page.bin' and g.toolbar  # keeps stock's toolbar, under it
    if path in TOOLBAR_SWITCHES and not plain:
        slider_plain(path, root)
        return encode(root)
    if stock and not plain:  # the rows alone, under the toolbar; the lists keep their boxes
        def table_rows(n):
            for prop in ('row_height', 'default_item_height'):
                if n[2].get(prop) == '78':
                    n[2][prop] = str(g.stock_row)
            for child in n[3]:
                table_rows(child)
        table_rows(root)
        return encode(root)
    if not playlist:
        nav[0][2]['visible'] = 'false'
        nav[0][2]['enable'] = 'false'
    else:  # the toolbar's right button keeps the iPod glyphs' corner clearance
        for n in nav[0][3]:
            if n[2].get('name') == 'gif_returnplay':
                n[1][0] = 375 - corner_x(g.bar, n[1][3]) - n[1][2]
    for n in root[3]:
        if n is nav[0]:
            continue
        kind, box, props, _ = n
        if kind == 'list_view' and settings:  # settings rows: see ipod_list_layout
            check(box[1] == 50 and props.get('default_item_height') == str(SET_STOCK_ROW) and
                    'item_height' not in props, f'{path}: unexpected settings list')
            row = g.stream_row if path == 'stream_page.bin' else g.set_row
            props['default_item_height'] = str(row)
            box[1], box[3] = g.set_top, geo.BOTTOM - g.set_top if g.list_end else g.set_rows * g.set_row // row * row
        elif kind in ('list_view', 'table_view', 'tab_control'):
            check(box[1] in (50, 100), f'{path}: unexpected list position')
            if not playlist:
                box[1] -= 50
            box[3] = geo.BOTTOM - box[1]
        elif props.get('name') == 'view_navbar_allplay':
            check(box == [0, 50, 375, 50], f'{path}: unexpected action bar')
            box[1] = 0
        elif kind == 'list_item':  # playlist editing actions
            box[1] = (10 if g.toolbar else 0) + ((box[1] - 60) // 78) * pitch  # navigation.c ringnav_playlistsmore
        elif props.get('name') == 'view':  # allmusic's stock empty-state panel
            box[1] -= 50
        elif path in NAVBAR_ONLY:  # settings panels and notes keep their size; text ends clear of a top corner
            check(box[1] >= 50, f'{path}: unexpected content under the toolbar')
            box[1] -= 50 - (g.set_row if path == SLEEP_TIMER and plain else 0)  # under the row its switch moves to
            if kind in ('label', 'hscroll_label') and corner_inset(g.bar + box[1]):
                box[2] = min(box[2], 375 - box[0] - corner_x(g.bar + box[1], 0))
    if path in NAVBAR_ONLY:
        slider_plain(path, root)
        return encode(root)
    if settings:  # a 20 px label in Update Library's row: it takes the row, whose font it wouldn't hold
        for n in walk(root):
            if n[0] == 'hscroll_label' and n[1][1:2] == [25] and n[1][3] == 20:
                n[1][1], n[1][3] = 0, SET_STOCK_BODY
        return encode(root)
    if path == 'localmusic/allmusic_page.bin' and g.action_row:
        songs_action_row(root, g)

    def rows(n, in_row=False):
        kind, box, props, children = n
        if path == 'localmusic/album_page.bin' and props.get('name') == 'button1':  # inline black grid buttons
            for key, value in props.items():
                if key.endswith(':bg_color') and value == '#000000':
                    props[key] = '#00000000'
        if kind == 'table_client' and props.get('self_layout', '').startswith('default(x=0,y=0,w=365,'):
            # the full width, as the Library's scroll_view: the scroll bar overlays the row's right
            # edge, and the selection card and chevrons line up with every other list
            props['self_layout'] = props['self_layout'].replace('w=365', 'w=375')
            box[2] = 375
        in_row = in_row or kind in ('list_item', 'table_row') and box[3] in (0, 78)
        for prop in ('row_height', 'default_item_height'):
            if props.get(prop) == '78':
                props[prop] = str(pitch)
        if kind == 'list_view' and props.get('default_item_height') == str(g.set_row) and 'item_height' not in props:
            props['item_height'] = props['default_item_height']  # not a settings list (navigation.c settings_list)
        if in_row:
            if kind in ('list_item', 'table_row') and box[3] == 78:
                box[3] = pitch
            elif box[3] == 70:
                box[3] = body
            if box[1] in (11, 14, 21, 40, 41):
                box[1] = text_y(g, box[1])  # titles and metadata at the set's y in its body
            elif box[1] in (9, 10) and kind == 'image':
                box[1] = inset
        if path == ARTIST_PAGE:
            if kind == 'pages':
                check(props.get('self_layout') == 'default(x=0,y=40,w=100%,h=170)', 'Unexpected tabs layout')
                props['self_layout'] = f'default(x=0,y=40,w=100%,h={geo.BOTTOM - 40})'
                box[3] = geo.BOTTOM - 40
            if props.get('name') == 'list_view_album':
                box[3] = geo.BOTTOM - 40
        for child in children:
            rows(child, in_row)
    rows(root)
    return encode(root)


def set_assets(cat, g):
    """The UI windows g draws differently from SET0, as <g.name>_<name>.bin beside the stock one.
    The prefix goes on the basename: stock opens subdirectory windows by path."""
    # ponytail: playing_page_stock gets no set copy; add it when a set needs one
    ui, out = 'release/assets/default/raw/ui/', {}
    for rel in UI_ASSETS | STOCK_ROWS:
        data = cat(ui + rel)
        mine = patch_asset(rel, data, g=g)
        if mine != patch_asset(rel, data):
            folder, _, base = rel.rpartition('/')
            out[ui + (folder and folder + '/') + f'{g.name}_{base}'] = mine
    return out


def patch_code(data, symbols):
    changes = []
    word = functools.partial(patch_word, data, changes=changes)

    # Lookup song handlers loop until Now Playing is on top; animated Back cannot finish
    # until they return. Close each named page synchronously with stock navigator_close.
    for address, name_slot in ((0x4a6250, 0x468), (0x4ad458, 0x468), (0x526e70, 0x420)):
        word(address, 0x8f99aeb0, 0x8f99a8f8, 'lookup unwind uses navigator_close')
        word(address + 4, 0x0320f809, 0x0320f809, 'lookup close call')
        word(address + 8, 0, 0x8fa40000 | name_slot, 'lookup close receives the top window name')

    values = immediate_values(SET0)
    for group in AUDIT['immediates']:
        value = values[group['value']] if isinstance(group['value'], str) else group['value']
        for address, instruction in group['sites']:
            old = int(instruction, 16)
            word(int(address, 16), old, (old & 0xffff0000) | value, group['purpose'])
    group = AUDIT['row_layout_calls']
    for address, instruction in group['sites']:
        word(int(address, 16), int(instruction, 16),
             jal(symbols['compact_set_row_layout']), group['purpose'])
    for name, call in (('stock_row_layout_calls', 'stock_set_row_layout'), ('stock_row_text_calls', 'stock_row_text'), ('stock_row_style_calls', 'stock_row_style')):
        for address, instruction in AUDIT[name]['sites']:
            word(int(address, 16), int(instruction, 16), jal(symbols[call]), AUDIT[name]['purpose'])
    word(0x522410, 0x0320f809, 0, 'folder rebind retains the width owned by its row layouter')
    # Playlist page's Import/Export tiles: no icon (0 wide), the label spans the 164px tile in
    # stock's centred s_scrlabel_white24c (0x769220) instead of 91px after the icon.
    for icon, style in ((0x4b2fa0, 0x4b3028), (0x4b30a4, 0x4b3130)):
        word(icon, 0x24070032, 0x24070000, 'playlist tile icon width 0')
        word(icon + 0x54, 0x2407005b, 0x240700a4, 'playlist tile label spans the tile')
        word(icon + 0x60, 0x2405003f, 0x24050000, 'playlist tile label at the tile edge')
        word(style, 0x25058c04, 0x25059220, 'playlist tile label centred')
    # iPod settings rows: the list_view layouter's layout slot (data, vtable 0x926928 + 8).
    word(inc('LIST_VIEW_LAYOUT_SLOT'), inc('LIST_VIEW_LAYOUT'), symbols['ipod_list_layout'],
         'settings lists stack SET_ROW rows and map their children')
    # toolsTimeItoa's formats, as an iPod writes times: 0:02 and 1:02:03, not 00:02 and 01:02:03.
    for address, old, new in ((0x7778a0, 0x64323025, 0x253a6425), (0x7778a4, 0x3230253a, 0x3a643230),
                              (0x7778a8, 0x30253a64, 0x64323025), (0x7778ac, 0x00006432, 0),
                              (0x7778b0, 0x64323025, 0x253a6425), (0x7778b4, 0x3230253a, 0x00643230),
                              (0x7778b8, 0x00000064, 0)):
        word(address, old, new, 'times without a leading zero')
    # home_page_init's memory-play resume opens Now Playing; outside car mode it runs the page's player_start alone.
    word(0x523de0, 0x0320f809, jal(symbols['ringnav_boot']),
         'boot resume restores the queue paused and stays on Home')
    return changes
