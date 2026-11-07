"""tools/ipod.py's page rewrites on small synthetic UI trees built to the shape each one checks for,
its UI, style, PNG and code readers and patchers on small synthetic files, and the ImageMagick calls
behind its icons, recorded instead of run."""
import copy
import dataclasses
import math
import re
import shutil
import struct
import subprocess
import zlib

import pytest

import ipod
import theme

CLASSIC_LIGHT, CLASSIC_LIGHT_TEXT = (theme.sets(theme.builtins()[1])[s] for s in ('classic-light', 'classic-light-text'))  # Classic's sets: its theme.xml


def w(name, *children, kind='view', at=None, **props):
    return [kind, at or [0, 0, 10, 10], {'name': name, **props}, list(children)]


def named(root):
    return {n[2].get('name'): n for n in ipod.walk(root)}


MAGIC = bytes.fromhex('12122211')


def test_decode():
    """A type fills up to 32 bytes, geometry is signed and properties are UTF-8."""
    data = (MAGIC + b'slider'.ljust(32, b'\0') + struct.pack('<4i', -200, 5, 30, 375) + 'text\0单曲\0name\0slider_play\0\0'.encode() +
            b'x' * 32 + struct.pack('<4i', 1, 2, 3, 4) + b'\0\0' + b'\0')
    assert ipod.decode(data) == ['slider', [-200, 5, 30, 375], {'text': '单曲', 'name': 'slider_play'}, [['x' * 32, [1, 2, 3, 4], {}, []]]]


@pytest.mark.parametrize('data,message', [
    (b'\x12\x12\x22\x12' + ipod.encode(w('view'))[4:], 'Unexpected AWTK UI magic'),
    (MAGIC + bytes(47), 'Truncated UI widget'),
    (MAGIC + b'view'.ljust(48, b'\0') + b'name\0a\0name\0b\0\0\0', 'Duplicate UI property'),
    (ipod.encode(w('view')) + b'\0', 'Trailing UI data'),
])
def test_decode_refuses(data, message):
    with pytest.raises(ValueError, match=f'^{message}$'):
        ipod.decode(data)


STATES = ('normal', 'disable', 'focused')


def clear_borders(props):
    return all(props[f'style:{s}:border_color'] == '#00000000' for s in STATES)


def local_card():
    return w('btn_localmusic',
             w('img_localmusic', kind='image', **{'style:normal:bg_image': 'menu_music', 'style:focused:bg_image': 'menu_musicdown', 'draw': 'menu_music'}),
             w('label_localmusic', kind='label', text='stock'))


def menu():
    return w('slide_menu', w('btn_playing'), local_card(), w('btn_folder'), w('btn_stream'), w('btn_playset'), w('btn_sysset'), kind='slide_menu')


def test_home_card():
    m = menu()
    ipod.home_card(m, 2, 'coverflow', 'Cover Flow')
    card = m[3][2]
    assert card[2]['name'] == 'btn_coverflow'
    image, label = card[3]
    assert image[2] == {'name': 'img_coverflow', 'style:normal:bg_image': 'menu_coverflow', 'style:focused:bg_image': 'menu_coverflowdown', 'draw': 'menu_music'}
    assert label[2] == {'name': 'label_coverflow', 'text': 'Cover Flow'}
    assert m[3][1] == local_card() and len(m[3]) == 7


@pytest.mark.parametrize('image,label', [('x', 'label_localmusic'), ('img_localmusic', 'x')])
def test_home_card_wants_the_local_music_card(image, label):
    m = menu()
    m[3][1][3][0][2]['name'], m[3][1][3][1][2]['name'] = image, label
    with pytest.raises(ValueError, match='^Unexpected Local Music card$'):
        ipod.home_card(m, 2, 'coverflow', 'Cover Flow')


def home():
    return w('home', menu(), w('img_left', kind='image'), w('img_right', kind='image'))


def test_ipod_home():
    root = home()
    ipod.ipod_home(root)
    assert [n[2]['name'] for n in root[3]] == ['img_homeart', 'list_view_home', 'list_view_homeset', 'slide_menu', 'img_left', 'img_right']
    art = root[3][0]
    assert art[:3] == ['image', [0, 0, 375, 290], {'name': 'img_homeart', 'draw_type': 'fill'}]
    assert root[3][2][2]['visible'] == 'false' and 'visible' not in root[3][1][2]
    rows = root[3][1][3][0][3]
    assert [r[2]['name'] for r in rows] == ['btn_' + r for r in ipod.HOME_ROWS]
    assert [r[2]['name'] for r in root[3][2][3][0][3]] == ['btn_playset', 'btn_sysset']
    carousel = root[3][3]
    assert [c[2]['name'] for c in carousel[3]] == ['btn_playing', 'btn_localmusic', 'btn_coverflow', 'btn_folder', 'btn_rockbox',
                                                   'btn_stream', 'btn_playset', 'btn_sysset']
    assert carousel[3][2][3][1][2]['text'] == 'Cover Flow' and carousel[3][4][3][1][2]['text'] == 'Rockbox'
    assert carousel[3][1][3][1][2] == {'name': 'label_library', 'text': 'Library'}
    assert carousel[3][2][3][1][2]['name'] == 'label_coverflow'
    for n in root[3][3:]:
        assert n[2]['visible'] == 'false' and n[2]['sensitive'] == 'false'


def test_ipod_home_wants_the_carousel():
    root = home()
    root[3].pop()
    with pytest.raises(ValueError, match='^Unexpected home carousel$'):
        ipod.ipod_home(root)


def test_ipod_home_wants_the_cards():
    root = home()
    root[3][0][3].pop()
    with pytest.raises(ValueError, match='^Unexpected home cards$'):
        ipod.ipod_home(root)


def battery():
    return w('label_battery', kind='label', at=[0, 0, 40, 30], **{f'style:{s}:font_size': '18' for s in STATES})


def bar():
    left = w('view_left', w('img_state', at=[0, 0, 20, 30]), w('label_eq', at=[0, 0, 50, 30]), w('img_vol', at=[5, 5, 30, 20]),
             w('label_vol', at=[6, 6, 30, 20]), at=[0, 0, 187, 30], children_layout='default(c=3,xm=50,s=5)')
    right = w('view_right', w('img_bt', at=[0, 0, 20, 30]), w('img_wifi', at=[0, 0, 40, 30]), battery(), w('img_battery', at=[0, 0, 30, 30]),
              w('img_synclink', at=[1, 1, 20, 20]), at=[187, 0, 188, 30], children_layout='default(c=3,xm=50,s=5)')
    return w('system_bar', left, right, at=[0, 0, 375, 30])


def test_status_bar_colours():
    root = bar()
    ipod.status_bar(root)
    assert root[2]['style:normal:bg_color'] == '#000000' and clear_borders(root[2])


def test_status_bar_widgets():
    root = bar()
    ipod.status_bar(root)
    n = named(root)
    assert n['img_wifi'][1][2] == 14 and n['label_eq'][1][2] == 28
    for s in STATES:
        assert n['label_eq'][2][f'style:{s}:font_size'] == '16'
        assert n['label_battery'][2][f'style:{s}:font_size'] == str(ipod.BATT_PCT_PX)
        assert n['label_battery'][2][f'style:{s}:text_align_h'] == 'right'
    assert n['label_battery'][1][2] == ipod.BATT_PCT_W and n['label_battery'][2]['visible'] == 'false'
    assert n['view_battery'][:3] == ['view', [0, 0, ipod.BATT_H_W, 0], {'name': 'view_battery', 'visible': 'false'}]
    left, right = root[3][:2]
    assert [c[2]['name'] for c in left[3]] == ipod.STATUS_LEFT and [c[2]['name'] for c in right[3]] == ipod.STATUS_RIGHT
    for view in (left, right):
        assert view[2]['children_layout'] == f'default(c=3,xm={ipod.STATUS_MARGIN},s={ipod.inc("STATUS_GAP")})'


def test_status_bar_hidden_stay_off_screen():
    root = bar()
    ipod.status_bar(root)
    n = named(root)
    assert [c[2]['name'] for c in root[3][2:5]] == ipod.STATUS_HIDDEN
    assert n['img_vol'][1] == [-200, 5, 30, 30] and n['label_vol'][1] == [-200, 6, 30, 30] and n['img_synclink'][1] == [-200, 1, 20, 30]


def test_status_bar_clock_and_idle_labels():
    root = bar()
    ipod.status_bar(root)
    idle, clock = root[3][-2:]
    assert idle == ['label', [ipod.STATUS_MARGIN, 0, 28, 30], {
        'name': 'label_idle', 'text': 'Q2', 'visible': 'false', 'style:normal:font_size': '18',
        'style:normal:text_color': '#FFFFFF', 'style:normal:text_align_h': 'left'}, []]
    gap = ipod.inc('STATUS_GAP')
    left = ipod.STATUS_MARGIN + 20 + 28 + gap
    right = ipod.STATUS_MARGIN + 20 + 14 + 30 + 2 * gap
    extent = max(left, right)
    assert clock == ['hscroll_label', [extent, 0, 375 - 2 * extent, 30], {
        'name': 'label_clock', 'style': 's_scrlabel_white20c', 'style:normal:font_size': '16', 'only_focus': 'true', 'ellipses': 'true'}, []]


def test_status_bar_wants_its_views():
    root = bar()
    root[3][0][2]['name'] = 'x'
    with pytest.raises(ValueError, match='^Unexpected status bar$'):
        ipod.status_bar(root)


def test_status_bar_wants_its_widgets():
    root = bar()
    root[3][0][3].pop()
    with pytest.raises(ValueError, match='^Unexpected status bar widgets$'):
        ipod.status_bar(root)


@pytest.mark.parametrize('state', STATES)
def test_status_bar_wants_the_stock_battery_size(state):
    root = bar()
    named(root)['label_battery'][2][f'style:{state}:font_size'] = '12'
    with pytest.raises(ValueError, match='^Unexpected battery label$'):
        ipod.status_bar(root)


@pytest.mark.parametrize('view', [0, 1])
def test_status_bar_wants_the_stock_layout(view):
    root = bar()
    root[3][view][2]['children_layout'] = 'default(c=3,xm=40,s=5)'
    with pytest.raises(ValueError, match='^Unexpected status bar layout$'):
        ipod.status_bar(root)


def test_status_bar_clock_must_fit():
    root = bar()
    named(root)['img_state'][1][2] = 80
    with pytest.raises(ValueError, match=r'^Status bar clock \d+px, too narrow$'):
        ipod.status_bar(root)


def test_status_bar_clock_exactly_the_minimum():
    root = bar()
    gap = ipod.inc('STATUS_GAP')
    extent = (375 - ipod.CLOCK_MIN) // 2
    named(root)['img_state'][1][2] = extent - ipod.STATUS_MARGIN - 28 - gap
    ipod.status_bar(root)
    assert named(root)['label_clock'][1][2] == ipod.CLOCK_MIN + (375 - ipod.CLOCK_MIN) % 2 or named(root)['label_clock'][1][2] >= ipod.CLOCK_MIN


def test_status_bar_right_view_must_reach_the_edge():
    root = bar()
    root[3][1][1][2] = 180
    with pytest.raises(ValueError, match='too narrow$'):
        ipod.status_bar(root)


def test_status_bar_battery_must_fit(monkeypatch):
    monkeypatch.setattr(ipod, 'BATT_ROOM', ipod.BATT_ROOM + 1)
    with pytest.raises(ValueError, match=r'^Status bar battery needs \d+px of \d+px$'):
        ipod.status_bar(bar())


def test_status_bar_battery_reach(monkeypatch):
    monkeypatch.setattr(ipod, 'BATT_PCT_W', 500)
    with pytest.raises(ValueError, match=r'^Status bar battery needs \d+px of \d+px$'):
        ipod.status_bar(bar())


@pytest.mark.parametrize('widest', ['BATT_PCT_W', 'BATT_H_W', 'BATT_INSIDE_W'])
def test_status_bar_battery_exactly_fits(monkeypatch, widest):
    room = ipod.BATT_ROOM
    gap, nub, width = ipod.inc('STATUS_GAP'), ipod.inc('BATT_NUB_W'), 80
    if widest == 'BATT_INSIDE_W':
        monkeypatch.setitem(ipod.O, 'BATT_INSIDE_W', width - nub)
    else:
        monkeypatch.setattr(ipod, widest, width)
    monkeypatch.setattr(ipod, 'BT_REACH', room - 2 * gap - 14 - width)
    ipod.status_bar(bar())
    monkeypatch.setattr(ipod, 'BT_REACH', room - 2 * gap - 14 - width + 1)
    with pytest.raises(ValueError, match=r'^Status bar battery needs \d+px of \d+px$'):
        ipod.status_bar(bar())


SLIDER = {'style:normal:bg_image': 'a', 'style:normal:fg_image': 'b', 'style:normal:icon': 'c', 'style:normal:y_offset': '6',
          'style:normal:bg_color': '#111', 'style:pressed:fg_color': '#222', 'style:normal:round_radius': '9', 'slide_with_bar': 'true'}


def test_plain_slider_is_used_by_np_slider():
    slider: list = ['slider', [0, 0, 1, 1], dict(SLIDER), []]
    ipod.np_slider(slider, 10, 100, 50, 4)
    assert slider[1] == [10, 100 - ipod.NP_TOUCH // 2, 50, ipod.NP_TOUCH]
    props = slider[2]
    assert not any(k.endswith((':bg_image', ':fg_image', ':icon', ':y_offset')) for k in props)
    assert props['style:normal:bg_color'] == ipod.NP_TRACK and props['style:pressed:fg_color'] == ipod.NP_FILL
    assert props['style'] == 's_ipod_progress' and props['bar_size'] == '4' and props['slide_with_bar'] == 'true'
    for state in ('normal', 'pressed', 'over', 'disable', 'focused'):
        assert props[f'style:{state}:border_color'] == '#00000000'
    assert props['style:normal:round_radius'] == '2'


def playing():
    buttons = w('view_buttons', w('img_return', at=[1, 2, 3, 4]),
                w('img_fav', kind='image', image='fav', x_offset='1', **{'style:normal:text_align_h': 'left', 'keep': 'yes'}),
                w('img_more', kind='image', image='more', y_offset='1'),
                w('img_playmode', kind='image', y_offset='1'))
    title = w('scrlabel_title', kind='hscroll_label', at=[9, 9, 9, 9], **{'style:normal:font_size': '24', 'style:disable:font_size': '24'})
    artist = w('scrlabel_artist', kind='hscroll_label', **{'style:normal:font_size': '20', 'style:normal:text_color': '#123456',
                                                           'style:normal:text_align_h': 'center', 'text': 'x'})
    lrc = w('view_lrc', w('scroll_lrc', kind='scroll_view', self_layout='default(x=0,y=0,w=-10,h=-20)'), self_layout='default(x=0,y=0,w=100%,h=178)')
    info = w('view_info', w('label_a', at=[5, 6, 7, 8]), w('label_b', at=[10, 6, 7, 8]))
    album = w('view_album', w('img_cover', kind='image'), w('img_playstate', kind='image'))
    slide = w('slide_view_view', w('slide_view', album, lrc, info, w('label_lyricmsg', at=[3, 4, 5, 6])),
              w('slide_indicator1', at=[0, 1, 2, 3], **{'style:normal:fg_color': '#1', 'style:normal:selected_fg_color': '#2'}))
    return w('playing_page', buttons, title, artist, w('label_playtime', **{'style:normal:text_color': '#1'}),
             w('label_playlen', at=[1, 1, 1, 1], **{'style:normal:text_color': '#1'}), slide,
             w('slider_play', kind='slider', **SLIDER), w('img_repeata', at=[1, 2, 3, 4]), w('img_repeatb', at=[5, 6, 7, 8]), w('image_wait'))


@pytest.fixture
def page():
    root = playing()
    ipod.playing_page(root)
    ipod.encode(root)  # whole pixels, or stock's format can't hold them
    return root, named(root)


def test_playing_page_top_row(page):
    root, n = page
    buttons = n['view_buttons']
    assert buttons[1] == [0, 0, 375, ipod.NP_TOP] and n['img_return'][1] == [-200, 2, 3, 4]
    x = ipod.NP_ICONS_END - ipod.NP_ICON
    assert n['img_more'][1] == [x, 0, ipod.NP_ICON, ipod.NP_TOP]
    assert n['img_more'][2] == {'name': 'img_more', 'image': 'more', 'draw_type': 'center'}
    assert n['img_playmode'][2] == {'name': 'img_playmode'}
    assert n['img_fav'][2] == {'name': 'img_fav', 'image': 'fav', 'keep': 'yes', 'draw_type': 'center'}
    assert [c[2]['name'] for c in buttons[3]] == ['img_return', 'img_more', 'label_ipod_pos']
    assert n['label_ipod_pos'][:3] == ['label', [ipod.NP_POS_X, 0, x - ipod.NP_POS_X, ipod.NP_TOP], {
        'name': 'label_ipod_pos', 'style:normal:font_size': '16', 'style:normal:text_color': ipod.NP_GREY, 'style:normal:text_align_h': 'left'}]


def test_playing_page_moves_controls_beside_the_art(page):
    _, n = page
    y = ipod.NP_ART_Y + ipod.NP_ART - ipod.NP_ICON + ipod.NP_ICON_PAD
    x = ipod.NP_ICONS_END - ipod.NP_ICON
    assert n['img_fav'][1] == [x, y, ipod.NP_ICON, ipod.NP_ICON]
    assert n['img_playmode'][1] == [x - ipod.NP_ICON - 6, y, ipod.NP_ICON, ipod.NP_ICON]
    assert [c[2].get('name') for c in n['view_album'][3]] == ['img_cover', 'img_playstate', 'scrlabel_title', 'scrlabel_artist',
                                                              'label_ipod_album', 'img_playmode', 'img_fav']


def test_playing_page_text_column(page):
    _, n = page
    y = ipod.NP_ART_Y
    icons = y + ipod.NP_ART - ipod.NP_ICON + ipod.NP_ICON_PAD
    top = y + (icons - ipod.NP_ICON_PAD - y - 84) // 2
    width = 375 - ipod.NP_MARGIN - ipod.NP_TEXT_X
    assert n['scrlabel_title'][1] == [ipod.NP_TEXT_X, top, width, 28]
    assert n['scrlabel_artist'][1] == [ipod.NP_TEXT_X, top + 34, width, 22]
    assert n['label_ipod_album'][1] == [ipod.NP_TEXT_X, top + 62, width, 22]
    title, artist, album = n['scrlabel_title'][2], n['scrlabel_artist'][2], n['label_ipod_album'][2]
    assert title['style'] == 's_scrlabel_white20l'
    for s in STATES:
        assert title[f'style:{s}:font_size'] == str(ipod.NP_TITLE_PX)
    assert artist['style:normal:font_size'] == str(ipod.NP_META_PX) and artist['style:normal:text_align_h'] == 'left'
    assert artist['style:normal:text_color'] == f'#{ipod.inc("NP_ARTIST_RGB"):06X}'
    assert album['style:normal:text_color'] == f'#{ipod.inc("NP_ALBUM_RGB"):06X}'
    assert album['name'] == 'label_ipod_album' and album['text'] == '' and artist['text'] == 'x'


def test_playing_page_art_and_spinners(page):
    _, n = page
    art, x, y = ipod.NP_ART, ipod.NP_MARGIN, ipod.NP_ART_Y
    assert n['img_cover'][1] == [x, y, art, art]
    assert n['img_playstate'][1] == [x + (art - 120) // 2, y + (art - 120) // 2, 120, 120]
    assert n['img_playstate'][2]['opacity'] == '0'
    assert n['image_wait'][1] == [x + (art - 54) // 2, ipod.NP_TOP + y + (art - 54) // 2, 54, 54]


def test_playing_page_lyrics_and_info(page):
    _, n = page
    assert n['label_lyricmsg'][1] == [3 + 75, 4, 5, 6]
    assert n['label_a'][1][0] == 80 and n['label_b'][1][0] == 85
    lrc = n['scroll_lrc'][2]
    assert lrc['self_layout'] == 'default(x=0,y=0,w=100%,h=100%)'
    assert lrc['children_layout'] == f'list_view(x=0,y={ipod.inc("LYRIC_SLOT")},s=0)'
    assert n['view_lrc'][2]['self_layout'] == 'default(x=0,y=0,w=375,h=178)'


def test_playing_page_swipe_area(page):
    _, n = page
    assert n['slide_view_view'][1] == [0, ipod.NP_TOP, 375, ipod.NP_SLIDE_H + 6]
    assert n['slide_view'][1] == [0, 0, 375, ipod.NP_SLIDE_H]
    dots = n['slide_indicator1']
    assert dots[1] == [0, ipod.NP_DOTS_Y, 2, 3]
    assert dots[2]['style:normal:fg_color'] == '#555555' and dots[2]['style:normal:selected_fg_color'] == '#eeeeee'
    assert dots[2]['self_layout'] == f'default(x=0,y={ipod.NP_DOTS_Y},w=100%,h=10)'
    assert dots[2]['anchor_x'] == '187px' and dots[2]['anchor_y'] == '0px'


def test_playing_page_progress_row(page):
    root, n = page
    x, y, w_, h = ipod.NP_BAR
    assert n['slider_play'][1] == [x, y + h // 2 - ipod.NP_TOUCH // 2, w_, ipod.NP_TOUCH]
    assert n['slider_play'][2]['bar_size'] == str(h)
    assert n['img_repeata'][1] == [x, 2, 3, 4] and n['img_repeatb'][1] == [x, 6, 7, 8]
    assert n['label_playtime'][1] == [ipod.NP_TIME_X, ipod.NP_TIMES_Y, 80, 16]
    assert n['label_ipod_remain'][1] == [375 - ipod.NP_TIME_X - 80, ipod.NP_TIMES_Y, 80, 16]
    assert n['label_ipod_remain'][2]['text'] == '' and 'visible' not in n['label_ipod_remain'][2]
    assert n['label_playlen'][2]['visible'] == 'false'
    assert n['label_playtime'][2]['style:normal:text_color'] == ipod.NP_GREY
    assert n['label_ipod_remain'][2]['style:normal:text_color'] == ipod.NP_GREY
    assert n['label_playlen'][2]['style:normal:text_color'] == '#1'


def test_playing_page_classic_ipod():
    """Classic (np_ipod): the art NP_IPOD_ART, up in the top row beside More, its box taking its reflection's
    room, the swipeable pages with it, their other content and the dots where they were on screen; More the
    window's last child, over the pages; "3 of 12" under the album; the times beside a bar narrowed for them,
    in the Classic face, centred on its rows, restacked over the art's page."""
    g = dataclasses.replace(ipod.SET0, np_ipod=1, bar=26)  # Classic's bar: its art comes down off the glass
    root, base = playing(), playing()
    ipod.playing_page(root, g)
    ipod.playing_page(base, dataclasses.replace(g, np_ipod=0))
    ipod.encode(root)
    n, b, (x, _, w_, _) = named(root), named(base), ipod.with_geometry(g).NP_BAR
    lift, view, cover = ipod.NP_IPOD_LIFT, n['slide_view_view'][1], n['img_cover'][1]
    assert view[1] == b['slide_view_view'][1][1] - lift
    top = g.bar + view[1] + cover[1] - ipod.inc('NP_IPOD_ART_DROP')  # on screen: NP_IPOD_ART_DROP under the first row whose corner clears the glass
    assert cover[0] == ipod.NP_MARGIN and cover[2:] == [ipod.NP_IPOD_ART, ipod.NP_IPOD_ART + ipod.NP_REFLECT]
    assert cover[1] > 0 and ipod.corner_x(top, 0) <= cover[0] < ipod.corner_x(top - 1, 0)
    assert n['slide_view'][1][3] == cover[1] + cover[3] <= view[3]
    for k in ('label_lyricmsg', 'view_lrc', 'slide_indicator1'):
        assert n[k][1][1] == b[k][1][1] + lift, k
    # the info page's rows, from the top row's end (the page's lift) to the dots, clear of the glass: each of stock's
    # labels the name, left, and a <name>_value beside it, right-aligned and ellipsized, its right edge mirroring x
    info, row = n['view_info'][3], ipod.NP_DOTS_Y // 2
    ix = max(g.text_x, ipod.corner_x(g.bar + ipod.NP_TOP, row), ipod.corner_x(g.bar + ipod.NP_TOP + row, row))
    assert [c[2]['name'] for c in info] == ['label_a', 'label_b', 'label_a_value', 'label_b_value']
    assert [c[1] for c in info] == [[ix, lift + row * i, ipod.NP_INFO_NAME_W, row] for i in range(2)] + [
        [ix + ipod.NP_INFO_NAME_W, lift + row * i, 375 - 2 * ix - ipod.NP_INFO_NAME_W, row] for i in range(2)]
    assert lift + 2 * row <= n['slide_indicator1'][1][1] and all(c[2]['style:normal:text_align_h'] == 'left' for c in info[:2])
    assert all(c[0] == 'hscroll_label' and c[2]['style'] == 's_scrlabel_white20l' and c[2]['ellipses'] == 'true' for c in info[:2])
    assert all(c[0] == 'hscroll_label' and c[2]['style'] == 's_scrlabel_white20r' and c[2]['ellipses'] == 'true' for c in info[2:])
    assert [c[1] for c in b['view_info'][3]] == [[5 + (375 - 225) // 2, 6, 7, 8], [10 + (375 - 225) // 2, 6, 7, 8]]  # the others' lines
    assert n['view_lrc'][2]['self_layout'] == f'default(x=0,y={lift},w=375,h=178)'
    assert root[3][-1] is n['img_more'] and n['img_more'][1] == b['img_more'][1]
    after = [c[2].get('name') for c in root[3]][root[3].index(n['slide_view_view']) + 1:][:2]
    assert after == ['label_playtime', 'label_ipod_remain']  # over the art's reflection
    title, album, pos = n['scrlabel_title'][1], n['label_ipod_album'][1], n['label_ipod_pos'][1]
    assert n['label_ipod_pos'] in n['view_album'][3] and pos[0] == album[0] == title[0] == cover[0] + cover[2] + 12
    assert view[1] + title[1] >= n['img_more'][1][3] - 2 and pos[1] == album[1] + 28
    assert pos[1] + pos[3] < n['img_fav'][1][1] + (ipod.NP_ICON - 28) // 2
    play, remain, bar = n['label_playtime'], n['label_ipod_remain'], ipod.inc('NP_BAR_IPOD')
    assert play[1][0] == ipod.corner_x(g.bar + play[1][1], play[1][3]) and x + w_ == 375 - x and w_ >= 170  # the times' rows clear the glass
    assert play[1][3] == ipod.NP_IPOD_TIME_H and play[1][2] == remain[1][2] == ipod.NP_IPOD_TIME_WS[0]
    assert play[1][0] + play[1][2] + ipod.NP_IPOD_TIME_GAP == x and remain[1][0] == x + w_ + ipod.NP_IPOD_TIME_GAP
    assert 0 <= 2 * (n['slider_play'][1][1] + (ipod.NP_TOUCH - bar) // 2) + bar - (2 * play[1][1] + play[1][3]) <= 1  # centred, or half a row high
    assert play[2]['style:normal:font_name'] == ipod.CLASSIC_LIGHT_FONT_NAME == remain[2]['style:normal:font_name']
    assert play[2]['style:normal:text_align_h'] == 'right' and remain[2]['style:normal:text_align_h'] == 'left'


def test_playing_page_final_order(page):
    root, _ = page
    assert [c[2].get('name') for c in root[3]] == ['view_buttons', 'label_playtime', 'label_playlen', 'label_ipod_remain',
                                                   'slide_view_view', 'slider_play', 'img_repeata', 'img_repeatb', 'image_wait']


def test_playing_page_wants_the_stock_children():
    root = playing()
    root[3].pop()
    with pytest.raises(ValueError, match='^Unexpected Now Playing page$'):
        ipod.playing_page(root)


def test_playing_page_wants_every_state_size():
    root = playing()
    named(root)['scrlabel_artist'][2]['style:normal:font_size'] = '20'
    root[3][1][2]['style:extra:font_size'] = '1'
    root[3][1][2]['style:other:font_size'] = '2'
    with pytest.raises(ValueError, match='^Now Playing text keeps a state size$'):
        ipod.playing_page(root)


@pytest.mark.parametrize('layout', ['default(x=1,y=0,w=-10,h=1)', 'other'])
def test_playing_page_wants_the_stock_lyrics(layout):
    root = playing()
    named(root)['scroll_lrc'][2]['self_layout'] = layout
    with pytest.raises(ValueError, match='^Unexpected lyrics scroll view$'):
        ipod.playing_page(root)


def test_playing_page_refuses_a_lyrics_layout():
    root = playing()
    named(root)['scroll_lrc'][2]['children_layout'] = 'x'
    with pytest.raises(ValueError, match='^Unexpected lyrics scroll view$'):
        ipod.playing_page(root)


def test_playing_page_three_lyric_lines_must_fit(monkeypatch):
    monkeypatch.setitem(ipod.O, 'LYRIC_SLOT', 60)
    with pytest.raises(ValueError, match='^Three lyric lines must fit the page$'):
        ipod.playing_page(playing())


def test_playing_page_three_lyric_lines_just_fit(monkeypatch):
    slot = 178 // 3
    monkeypatch.setitem(ipod.O, 'LYRIC_SLOT', slot)
    ipod.playing_page(playing())


def stock_page():
    slider = dict(SLIDER)
    root = w('playing_page',
             w('slide_view_view', w('slide_view', w('view_lrc', self_layout='default(x=0,y=0,w=100%,h=178)'),
                                    w('label_lyricmsg', at=[0, 73, 225, 28]), w('scroll_lrc', self_layout='default(x=0,y=0,w=-10,h=-20)'),
                                    at=[0, 0, 225, 198]), at=[80, 0, 225, 290]),
             w('scrlabel_title'), w('scrlabel_artist'),
             w('img_cover', at=[18, 0, 188, 188]), w('img_playstate', at=[52, 34, 120, 120]),
             w('img_fav', at=[2, 129, 86, 73], draw_type='center'),
             w('img_playmode', at=[0, 129, 86, 73], draw_type='default', x_offset='12', y_offset='3', other='kept'),
             w('slider_play', kind='slider', at=[50, 208, 290, 82], **slider))
    return root


@pytest.fixture
def audited(monkeypatch):
    def install(root):
        data = ipod.encode(root)
        monkeypatch.setitem(ipod.UI_ASSETS, ipod.PLAYING_PAGE, ipod.sha(data))
        return data
    return install


def test_playing_stock(audited):
    n = named(ipod.decode(ipod.playing_stock(audited(stock_page()))))
    assert n['scroll_lrc'][2]['self_layout'] == 'default(x=0,y=0,w=100%,h=100%)'
    assert n['view_lrc'][2]['self_layout'] == 'default(x=0,y=0,w=100%,h=-20)'
    assert n['label_lyricmsg'][2]['self_layout'] == 'default(x=0,y=73,w=100%,h=28)'
    assert n['img_cover'][1] == [22, 8, 180, 180] and n['img_playstate'][1] == [52, 38, 120, 120]
    assert n['img_playmode'][1] == [375 - 2 - 86, 129, 86, 73]
    assert n['img_playmode'][2] == {'name': 'img_playmode', 'draw_type': 'center', 'other': 'kept'}
    assert n['img_fav'][1] == [2, 132, 86, 73]
    assert n['slider_play'][1] == [50, 208 + 41 + 6 - ipod.NP_TOUCH // 2, 290, ipod.NP_TOUCH]
    assert n['slider_play'][2]['bar_size'] == str(ipod.NP_BAR[3])


def test_playing_stock_is_audited(audited):
    audited(stock_page())
    with pytest.raises(ValueError, match=r'^playing_page.bin: unaudited UI asset$'):
        ipod.playing_stock(b'other')


@pytest.mark.parametrize('name,index,value,message', [
    ('playing_page', 'name', 'x', 'Unexpected stock Now Playing'),
    ('slide_view', 'width', 224, 'Unexpected stock Now Playing'),
    ('scroll_lrc', 'self_layout', 'default(x=1,y=0,w=-10,h=-20)', 'Unexpected lyrics scroll view'),
    ('slide_view_view', 'width', 224, 'Unexpected stock lyrics page'),
    ('slide_view', 'geometry', [0, 0, 225, 199], 'Unexpected stock lyrics page'),
    ('view_lrc', 'self_layout', 'x', 'Unexpected stock lyrics page'),
    ('label_lyricmsg', 'geometry', [0, 74, 225, 28], 'Unexpected stock lyrics page'),
    ('label_lyricmsg', 'self_layout', 'x', 'Unexpected stock lyrics page'),
    ('img_cover', 'geometry', [18, 1, 188, 188], 'Unexpected stock cover'),
    ('img_playstate', 'geometry', [52, 35, 120, 120], 'Unexpected stock cover'),
    ('img_fav', 'geometry', [2, 130, 86, 73], 'Unexpected stock heart or play mode'),
    ('img_fav', 'draw_type', 'x', 'Unexpected stock heart or play mode'),
    ('img_playmode', 'draw_type', 'x', 'Unexpected stock heart or play mode'),
    ('slider_play', 'geometry', [50, 209, 290, 82], 'Unexpected stock progress bar'),
    ('slider_play', 'style:normal:y_offset', '7', 'Unexpected stock progress bar'),
])
def test_playing_stock_wants_the_stock_page(audited, name, index, value, message):
    root = stock_page()
    node = named(root)[name]
    if index == 'width':
        node[1][2] = value
    elif index == 'geometry':
        node[1] = value
    else:
        node[2][index] = value
    with pytest.raises(ValueError, match=f'^{message}$'):
        ipod.playing_stock(audited(root))


def cover():
    return w('bigcover', w('img_bigcover', kind='image', self_layout='default(x=0,y=0,w=100%,h=100%)', draw_type='scale_auto'),
             w('view_shade', at=[0, 270, 375, 50]), w('scrlabel_title', at=[42, 281, 290, 28]), fullscreen='true')


def test_bigcover_page():
    root = cover()
    ipod.bigcover_page(root)
    art, shade, title = root[3]
    size = ipod.BC_ART
    x, y = (375 - size) // 2, (320 - size) // 2
    assert art[1] == [x, y, size, size] and art[2]['self_layout'] == f'default(x={x},y={y},w={size},h={size})'
    foot = y + size - 50
    assert shade[1] == [x, foot, size, 50]
    assert title[1] == [x + ipod.BC_TITLE_PAD, foot + 11, size - 2 * ipod.BC_TITLE_PAD, 28]


@pytest.mark.parametrize('change,message', [
    (lambda r: r[2].update(fullscreen='false'), 'Unexpected full-screen art page'),
    (lambda r: r[3].pop(), 'Unexpected full-screen art page'),
    (lambda r: r[3][0][2].update(self_layout='x'), 'Unexpected full-screen art layout'),
    (lambda r: r[3][0][2].update(draw_type='x'), 'Unexpected full-screen art layout'),
    (lambda r: r[3][1].__setitem__(1, [0, 271, 375, 50]), 'Unexpected full-screen art layout'),
    (lambda r: r[3][2].__setitem__(1, [42, 282, 290, 28]), 'Unexpected full-screen art layout'),
])
def test_bigcover_page_wants_stock(change, message):
    root = cover()
    change(root)
    with pytest.raises(ValueError, match=f'^{message}$'):
        ipod.bigcover_page(root)


def quick():
    icons, labels = [], []
    for i, (icon, label) in enumerate(ipod.QS_GRID.items()):
        x, y = 20 + i % 4 * 85, (0, 112)[i // 4]
        icons.append(w('img_' + icon, kind='image', at=[x, y, ipod.QS_ICON, ipod.QS_ICON]))
        labels.append(w('label_' + label, kind='label', style='s_label_white18c'))
    slider = w('slider_backlight', kind='slider', slide_with_bar='true', **{'style:normal:bg_color': '#1', 'style:normal:fg_color': '#2'})
    light = w('view_backlight', slider, w('image0', kind='image', at=[0, 0, ipod.QS_SUN, 9]), w('image1', kind='image', at=[0, 0, ipod.QS_SUN, 9]))
    return w('statusbar_dialog', w('view_menu', *icons, *labels), light)


def test_quick_settings_grid():
    root = quick()
    ipod.quick_settings(root)
    ipod.encode(root)
    n = named(root)
    assert n['view_menu'][1] == [0, ipod.QS_TOP, 375, 2 * ipod.QS_PITCH - ipod.QS_ROW_GAP]
    for i, (icon, label) in enumerate(ipod.QS_GRID.items()):
        row = i // 4
        x = 20 + i % 4 * 85
        assert n['img_' + icon][1] == [x, row * ipod.QS_PITCH, ipod.QS_ICON, ipod.QS_ICON]
        assert n['label_' + label][1] == [x + (ipod.QS_ICON - ipod.QS_LABEL_W) // 2, row * ipod.QS_PITCH + ipod.QS_ICON + ipod.QS_LABEL_GAP,
                                          ipod.QS_LABEL_W, ipod.QS_LABEL_H]
        assert n['label_' + label][2]['style'] == 's_label_white16c' and n['label_' + label][2]['style:normal:text_align_v'] == 'top'


def test_quick_settings_brightness_row():
    root = quick()
    ipod.quick_settings(root)
    n = named(root)
    y = ipod.QS_TOP + 2 * ipod.QS_PITCH - ipod.QS_ROW_GAP + ipod.QS_ROW_GAP
    assert n['view_backlight'][1] == [0, y, 375, ipod.QS_TOUCH]
    sun_y = (ipod.QS_TOUCH - ipod.QS_SUN) // 2
    assert n['image0'][1] == [ipod.QS_EDGE, sun_y, ipod.QS_SUN, ipod.QS_SUN]
    assert n['image1'][1] == [375 - ipod.QS_EDGE - ipod.QS_SUN, sun_y, ipod.QS_SUN, ipod.QS_SUN]
    left = ipod.QS_EDGE + ipod.QS_SUN + 12
    assert n['slider_backlight'][1] == [left, 0, 375 - 2 * left, ipod.QS_TOUCH]
    props = n['slider_backlight'][2]
    assert props['self_layout'] == f'default(x={left},y=0,w={375 - 2 * left},h={ipod.QS_TOUCH})'
    assert props['dragger_size'] == str(ipod.QS_TOUCH) and props['bar_size'] == str(ipod.QS_BAR)
    assert props['style:normal:bg_color'] == ipod.NP_TRACK and props['style:normal:fg_color'] == ipod.NP_FILL


@pytest.mark.parametrize('change,message', [
    (lambda r: r[3][0][2].update(name='x'), 'Unexpected quick settings'),
    (lambda r: r[3][1][2].update(name='x'), 'Unexpected quick settings'),
    (lambda r: r[3][0][3].pop(), 'Unexpected quick settings controls'),
    (lambda r: named(r)['img_lock'][1].__setitem__(2, 50), 'lock: unexpected quick settings control'),
    (lambda r: named(r)['img_lock'][1].__setitem__(3, 50), 'lock: unexpected quick settings control'),
    (lambda r: named(r)['img_lock'][1].__setitem__(1, 1), 'lock: unexpected quick settings control'),
    (lambda r: named(r)['img_usbmode'][1].__setitem__(1, 0), 'usbmode: unexpected quick settings control'),
    (lambda r: named(r)['label_lock'][2].update(style='x'), 'lock: unexpected quick settings control'),
    (lambda r: named(r)['image0'][2].update(name='x'), 'Unexpected brightness row'),
    (lambda r: named(r)['image0'][1].__setitem__(2, 1), 'Unexpected brightness row'),
    (lambda r: named(r)['image1'][1].__setitem__(2, 1), 'Unexpected brightness row'),
    (lambda r: named(r)['slider_backlight'][2].update(slide_with_bar='false'), 'Unexpected brightness slider'),
])
def test_quick_settings_wants_stock(change, message):
    root = quick()
    change(root)
    with pytest.raises(ValueError, match=f'^{message}$'):
        ipod.quick_settings(root)


def test_quick_settings_volume_panel_must_cover_brightness(monkeypatch):
    monkeypatch.setitem(ipod.O, 'VOL_PANEL_Y', ipod.inc('VOL_PANEL_Y') + 1)
    with pytest.raises(ValueError, match='^The volume panel must cover the brightness row$'):
        ipod.quick_settings(quick())


def dialog():
    return w('confirminfo_dialog', w('img_cancel', kind='image', at=[0, 0, 80, 80], other='x'), w('img_enter', kind='image', at=[0, 0, 80, 80]))


def test_confirm_dialog():
    root = dialog()
    ipod.confirm_dialog(root)
    assert clear_borders(root[2])
    for i, (n, text) in enumerate(zip(root[3], ('Cancel', 'OK'))):
        assert n[0] == 'button' and n[1] == [0, ipod.CONFIRM_TOP + i * ipod.CONFIRM_ROW, 375, ipod.CONFIRM_ROW]
        assert 'other' not in n[2]
        assert (n[2]['name'], n[2]['text'], n[2]['focusable']) == (('img_cancel', 'img_enter')[i], text, 'false')
        assert n[2]['style:normal:font_size'] == '20' and n[2]['style:normal:text_align_h'] == 'center'
        for state in ('normal', 'pressed', 'over', 'focused'):
            assert n[2][f'style:{state}:bg_color'] == ('#333333' if state == 'pressed' else '#00000000')
            assert n[2][f'style:{state}:border_color'] == '#00000000' and n[2][f'style:{state}:border_width'] == '0'
            assert n[2][f'style:{state}:text_color'] == '#FFFFFF'


@pytest.mark.parametrize('change', [lambda r: r[3][0][2].update(name='x'), lambda r: r[3].pop(),
                                    lambda r: r[3][1][1].__setitem__(2, 79), lambda r: r[3][0][1].__setitem__(3, 79)])
def test_confirm_dialog_wants_stock(change):
    root = dialog()
    change(root)
    with pytest.raises(ValueError, match='^Unexpected confirm dialog$'):
        ipod.confirm_dialog(root)


def settings_page(*images):
    return w('page', w('holder', w('slider', kind='slider', **SLIDER)),
             *[w(f'img{i}', kind='image', at=[1, 2, 3, 4], image=name) for i, name in enumerate(images)],
             w('other', kind='image', at=[5, 6, 7, 8], image='keep'), w('plain', kind='label', image='vol_left'))


@pytest.mark.parametrize('left,right', [('drop_lighleft', 'drop_lightright'), ('balance_left', 'balance_right'), ('vol_left', 'vol_right')])
def test_slider_plain(left, right):
    root = settings_page(left, right)
    ipod.slider_plain(ipod.SETTINGS_SLIDERS[0], root)
    n = named(root)
    slider = n['slider'][2]
    assert 'style:normal:bg_image' not in slider and slider['bar_size'] == '8'
    assert slider['style:normal:bg_color'] == ipod.NP_TRACK and slider['style:pressed:fg_color'] == ipod.NP_FILL
    for state in ('normal', 'pressed', 'over', 'disable', 'focused'):
        assert slider[f'style:{state}:round_radius'] == '4' and slider[f'style:{state}:border_color'] == '#00000000'
    for name in ('img0', 'img1'):
        assert 'image' not in n[name][2] and n[name][1] == [1, 2, 0, 0]
    assert n['other'][2]['image'] == 'keep' and n['other'][1] == [5, 6, 7, 8] and n['plain'][2]['image'] == 'vol_left'


def test_slider_plain_other_pages_stay():
    root = settings_page('vol_left', 'vol_right')
    before = copy.deepcopy(root)
    ipod.slider_plain('other_page.bin', root)
    assert root == before


def test_slider_plain_wants_two_endpoints(monkeypatch):
    monkeypatch.setattr(ipod, 'SETTINGS_SLIDERS', ('x',))
    with pytest.raises(ValueError, match=r'^x: unexpected slider endpoints$'):
        ipod.slider_plain('x', settings_page('vol_left'))


def test_sized_art(monkeypatch):
    calls = []
    monkeypatch.setattr(ipod, 'imagemagick', lambda *args, data: calls.append((args, data)) or b'png')
    assert ipod.sized_art(b'in', 52) == b'png'
    assert ipod.sized_art(b'in', (140, 100)) == b'png'
    assert calls == [(('png:-', '-filter', 'Lanczos', '-resize', '52x52!', '-background', 'none', '-gravity', 'center', '-extent', '52x52', *ipod.PNG_OUT), b'in'),
                     (('png:-', '-filter', 'Lanczos', '-resize', '100x100!', '-background', 'black', '-gravity', 'center', '-extent', '140x100', *ipod.PNG_OUT), b'in')]


SET0_VALUES = dict(BOTTOM=290, PITCH=72, BODY=68, ART=52, ART_INSET=8, NP_POS_X=22, NP_ICONS_END=353,
                   NP_BAR_X=21, NP_BAR=[21, 253, 333, 4], NP_TIME_X=46)


def test_with_geometry_set0_matches_module():
    assert vars(ipod.with_geometry(ipod.SET0)) == SET0_VALUES
    assert {n: getattr(ipod, n) for n in SET0_VALUES} == SET0_VALUES   # today's names still import
    assert ipod.SET0 == ipod.Geometry() and vars(ipod.IPOD) == SET0_VALUES and ipod.SET0.name == 'ipod'


def test_with_geometry_follows_the_bar():
    assert vars(ipod.with_geometry(ipod.Geometry(name='t', bar=26))) == dict(
        SET0_VALUES, BOTTOM=294, NP_ICONS_END=350, NP_BAR_X=18, NP_BAR=[18, 253, 339, 4], NP_TIME_X=39)


def test_with_geometry_takes_the_row_sizes():
    ns = ipod.with_geometry(ipod.Geometry(name='t', pitch=64, body=60, art=48, art_inset=6))
    assert (ns.PITCH, ns.BODY, ns.ART, ns.ART_INSET, ns.BOTTOM) == (64, 60, 48, 6, 290)


T26 = ipod.Geometry(name='t', bar=26, pitch=64, body=60, art=52, art_inset=4)


def test_ipod_home_art_fills_the_client_height():
    root = home(); ipod.ipod_home(root, g=T26)
    assert root[3][0][1] == [0, 0, 375, 294]


def test_classic_light_home_rows_are_32():
    root = home(); ipod.ipod_home(root, g=CLASSIC_LIGHT); n = named(root)
    assert n['list_view_home'][1] == [0, ipod.HOME_TOP, 375, 7 * 32] and n['list_view_home'][2]['item_height'] == '32'
    rows = named(n['list_view_home'])  # the carousel's cards take the same names
    assert [rows['btn_' + r][1] for r in ipod.HOME_ROWS] == [[0, 32 * i, 375, 32] for i in range(7)]
    assert n['list_view_homeset'][1] == [0, ipod.HOME_TOP, 375, 2 * 32] and n['list_view_homeset'][2]['item_height'] == '32'
    root = home(); ipod.ipod_home(root); assert named(root)['list_view_home'][2]['item_height'] == '39'


def test_status_bar_takes_the_bar_height():
    root = bar(); ipod.status_bar(root, g=T26); n = named(root)
    assert [root[1][3], n['view_left'][1][3], n['view_right'][1][3]] == [26, 26, 26]
    assert n['label_idle'][1] == [ipod.STATUS_MARGIN, 0, 28, 26] and n['label_clock'][1][3] == 26
    assert [n[x][1][3] for x in ipod.STATUS_HIDDEN] == [26, 26, 26]


def test_status_bar_wants_the_stock_height():
    root = bar(); root[1][3] = 32
    with pytest.raises(ValueError, match='^Unexpected status bar height$'):
        ipod.status_bar(root)


# Classic's bar, from the spec's rules: bar text at bar_px (21 px, caps on rows 6 to 16), centred on the rows
# above the bar's two lines; title and clock share one box height and y (-1, the baseline at row 17); the
# title's text starts where the glass ends at the caps' top, g.bar_title_w wide; stock's hscroll_label keeps 2 px
# each side of the box, so the box is 4 wider; the clock is centred on the screen.
def test_fallback_scale_matches_the_faces():  # theme_font's bold.ttf, at the Classic face's cap height: stb's scale, a font's ascent - descent its size
    from fontTools.ttLib import TTFont
    def cap(path):
        f = TTFont(path, lazy=True)
        return f['OS/2'].sCapHeight / (f['hhea'].ascent - f['hhea'].descent)
    pct = re.search(r'#define FACE_FALLBACK_PCT (\d+)', (ipod.ROOT/'patch/navigation.c').read_text())
    assert pct
    assert int(pct[1]) == round(100*cap(ipod.ROOT/'assets/fonts/texgyreheros-bold.otf')/cap(ipod.ROOT/'assets/fonts/LiberationSans-Bold.ttf')) == 83


def test_status_metrics_centre_the_caps_in_the_classic_light_bar():
    g = CLASSIC_LIGHT
    m = ipod.status_metrics(g, ipod.CLASSIC_LIGHT_FONT_SRC)
    assert m['px'] == g.bar_px == 21 and m['cap'] == 11 and m['cap_top'] == 6 and m['label_y'] == -1
    assert m['inset'] == ipod.corner_inset(6) + ipod.BAR_PAD == 59  # BAR_PAD clear of the glass at the caps' top
    assert m['clock_text'] == 64 and classic_light_text_w('12:59 PM') <= 64  # its numbers only: Minimal's and Classic's keep CLOCK_TEXT
    assert ipod.CLOCK_TEXT == 70
    assert m['title'] == [57, -1, 95, g.bar - 1]  # text on columns 59 to 150; a 12-hour clock's starts at 155
    assert 2 * m['clock'][0] + m['clock'][2] == 375 and m['title'][1::2] == m['clock'][1::2] == [m['label_y'], g.bar - 1]
    # what a title fits: Local Songs and Now Playing, whole
    assert classic_light_text_w('Local Songs') <= m['title'][2] - 4 and classic_light_text_w('Now Playing') <= m['title'][2] - 4


def classic_light_text_w(text):  # text's advance in the Classic face at the bar's px (stb scales ascent - descent to the size)
    from fontTools.ttLib import TTFont
    f = TTFont(ipod.CLASSIC_LIGHT_FONT_SRC, lazy=True)
    cmap = f.getBestCmap()
    return sum(f['hmtx'][cmap[ord(c)]][0] for c in text) * CLASSIC_LIGHT.bar_px / (f['hhea'].ascent - f['hhea'].descent)


def test_classic_light_status_bar_has_a_title_and_no_idle_mark():
    root = bar(); ipod.status_bar(root, g=CLASSIC_LIGHT); n = named(root)
    m = ipod.status_metrics(CLASSIC_LIGHT, ipod.CLASSIC_LIGHT_FONT_SRC)
    assert n['label_title'][1] == m['title'] and n['label_clock'][1] == m['clock'] and 'label_idle' not in n
    assert [root[1][3], n['view_left'][1][3], n['view_right'][1][3], n['img_state'][1][3]] == [26, 22, 22, 22]
    for name in ('label_title', 'label_clock'):
        assert {n[name][2][f'style:{s}:{k}'] for s in ('normal', 'disable', 'focused') for k in ('font_size', 'font_name')} \
            == {str(CLASSIC_LIGHT.bar_px), ipod.CLASSIC_LIGHT_FONT_NAME}
    assert n['view_left'][3] == [] and n['view_left'][2]['children_layout'].endswith(f'xm={m["inset"]},s={ipod.inc("STATUS_GAP")})')


# Right of the centred clock, left to right: EQ (it floats; only while nothing plays, navigation.c
# bar_state_sync), Bluetooth, Wi-Fi, the play state and the battery, the battery body ending where the glass
# does at its top row. On Home's own list the play state is img_home_state, hidden until the payload shows it,
# in the title's place at HOME_STATE_X, on the right group's rows, the same art.
def test_classic_light_status_bar_battery_and_play_state_right():
    root = bar(); ipod.status_bar(root, g=CLASSIC_LIGHT); n = named(root)
    assert [c[2]['name'] for c in n['view_right'][3]] == ['label_eq', 'img_bt', 'img_wifi', 'img_state', *ipod.STATUS_RIGHT[2:]]
    home, state = n['img_home_state'], n['img_state']
    assert home in root[3] and home[1] == [ipod.HOME_STATE_X, n['view_right'][1][1], ipod.STATE_W, state[1][3]]
    assert home[2] == {**state[2], 'name': 'img_home_state', 'visible': 'false'}
    assert n['view_right'][2]['children_layout'].endswith(f'xm=60,s={ipod.inc("STATUS_GAP")})')  # the nub ends at x 314
    assert 375 - ipod.corner_inset(5) - 1 - (314 - ipod.inc('BATT_NUB_W')) == ipod.BAR_PAD  # the body's top-right pixel, row 5, clear of the glass; the nub's rows lower still
    assert [c[2]['name'] for c in root[3] if c[1][0] == -200] == ipod.STATUS_HIDDEN  # only these parked


# EQ floats inside the room the battery's reach counts, not left of img_bt's 43 px canvas (whose ink is its last
# BT_REACH px), where the layout would put it: past the clock's text. In the bar's face, on its text line.
def test_classic_light_status_bar_eq_in_the_room():
    root = bar(); ipod.status_bar(root, g=CLASSIC_LIGHT); n = named(root)
    m, eq = ipod.status_metrics(CLASSIC_LIGHT, ipod.CLASSIC_LIGHT_FONT_SRC), n['label_eq']
    x, gap = n['view_right'][1][0] + eq[1][0], ipod.inc('STATUS_GAP')
    edge = 315  # the group's right edge: the nub ends at x 314
    assert eq[2]['floating'] == 'true' and eq[1][1:] == [m['title'][1], ipod.CLASSIC_LIGHT_EQ_W, m['title'][3]]
    assert x == 222 and x >= 375 / 2 + m['clock_text'] / 2 + ipod.CLASSIC_LIGHT_CLOCK_GAP  # the widest clock, CLASSIC_LIGHT_CLOCK_GAP clear
    assert classic_light_text_w('EQ') <= ipod.CLASSIC_LIGHT_EQ_W  # the bar's face fits its box
    # 2 px short of Bluetooth's ink: the battery with its soft edge and nub, Wi-Fi, BT_REACH
    assert x + ipod.CLASSIC_LIGHT_EQ_W + 2 <= edge - (1 + CLASSIC_LIGHT.batt_w + ipod.inc('BATT_NUB_W')) - gap - n['img_wifi'][1][2] - gap - ipod.BT_REACH
    assert {eq[2][f'style:{s}:{k}'] for s in ('normal', 'disable', 'focused') for k in ('font_size', 'font_name')} \
        == {str(CLASSIC_LIGHT.bar_px), ipod.CLASSIC_LIGHT_FONT_NAME}


# Where the row layout puts the icons: right-aligned from the group's margin, the slot 29 (its soft edge, the body,
# the nub), hidden children taking no room. Bluetooth's and Wi-Fi's canvases are stock's; their ink lands on the
# columns and rows Classic's look states, and with the play state hidden they close up on the battery.
def test_classic_light_status_bar_icons_on_their_columns():
    root = bar(); ipod.status_bar(root, g=CLASSIC_LIGHT); n = named(root)
    view = n['view_right']
    assert view[1][1::2] == [0, 22]  # the 16 px canvases at y 3, centred on the battery's row 11; the payload moves Bluetooth a row down, Wi-Fi a row up (bar.icons)
    assert n['img_state'][2].get('floating') is None
    def lay(*names):
        x, out = 315, {}
        for name in reversed(names):
            w = 29 if name == 'view_battery' else n[name][1][2]
            x -= w
            out[name] = (x, x + w)
            x -= ipod.inc('STATUS_GAP')
        return out
    full = lay('img_bt', 'img_wifi', 'img_state', 'view_battery')
    assert full['img_bt'][1] == 242 and {k: v for k, v in full.items() if k != 'img_bt'} == {
        'view_battery': (286, 315), 'img_state': (264, 282), 'img_wifi': (246, 260)}
    assert full['img_bt'][1] - ipod.BT_REACH == 232  # Bluetooth's ink
    bare = lay('img_bt', 'img_wifi', 'view_battery')
    assert bare['img_wifi'] == (268, 282) and bare['img_bt'][1] - ipod.BT_REACH == 254
    assert n['label_eq'][1][0] + view[1][0] + 28 < 254  # EQ, widest with a 12-hour clock: 222 to 250


def test_classic_light_status_bar_battery_must_fit():
    ipod.status_bar(bar(), g=CLASSIC_LIGHT)  # 88 of 93
    with pytest.raises(ValueError, match=r'^Status bar battery needs 94px of 93px$'):
        ipod.status_bar(bar(), g=dataclasses.replace(CLASSIC_LIGHT, batt_w=CLASSIC_LIGHT.batt_w + 6))


def test_playing_page_follows_the_bar():
    root = playing(); ipod.playing_page(root, g=T26); n = named(root)
    assert n['img_more'][1] == [300, 0, 50, 40] and n['label_ipod_pos'][1] == [22, 0, 278, 40]
    assert n['slider_play'][1][0::2] == [18, 339] and n['img_repeata'][1][0] == 18
    assert n['label_playtime'][1] == [39, 265, 80, 16] and n['label_ipod_remain'][1] == [256, 265, 80, 16]


def test_patch_asset_rows_and_lists_follow_the_geometry(monkeypatch):
    page = w('folder_page', w('view_navbar', at=[0, 0, 375, 50]),
             w('list_view', w('row', w('art', kind='image', at=[8, 9, 52, 52]), kind='list_item', at=[0, 0, 375, 78]),
               kind='list_view', at=[0, 50, 375, 240], default_item_height='78'), kind='window')
    data = ipod.encode(page)
    monkeypatch.setitem(ipod.UI_ASSETS, 'folder_page.bin', ipod.sha(data))
    n = named(ipod.decode(ipod.patch_asset('folder_page.bin', data, g=T26)))
    assert n['list_view'][1] == [0, 0, 375, 294] and n['list_view'][2]['default_item_height'] == '72'
    assert n['row'][1][3] == 72 and n['art'][1][1] == 4
    n = named(ipod.decode(ipod.patch_asset('folder_page.bin', data)))
    assert n['list_view'][1] == [0, 0, 375, 290] and n['row'][1][3] == 72 and n['art'][1][1] == 8


# Streaming follows Row Art: 52 px rows under classic-light, 28 under classic-light-text, SET0's settings rows as before.
# The list keeps the settings lists' box: a set with list_end's takes the window.
def test_stream_page_rows_per_set(monkeypatch):
    page = w('stream_page', w('view_navbar', at=[0, 0, 370, 50]),
             w('list_view', kind='list_view', at=[0, 50, 375, 240], default_item_height=str(ipod.SET_STOCK_ROW)), kind='window')
    data = ipod.encode(page)
    monkeypatch.setitem(ipod.UI_ASSETS, 'stream_page.bin', ipod.sha(data))
    for g, row, h in ((ipod.SET0, ipod.SET_ROW, ipod.SET_ROWS * ipod.SET_ROW), (CLASSIC_LIGHT, 52, 294), (CLASSIC_LIGHT_TEXT, 32, 294)):
        assert g.stream_row == row
        n = named(ipod.decode(ipod.patch_asset('stream_page.bin', data, g=g)))
        assert n['list_view'][2]['default_item_height'] == str(row) and n['list_view'][1][3] == h, g.name


PAGE_T = ipod.Geometry(name='t', pitch=28, body=26, art_pitch=52, art_body=50, art=44, art_inset=3,
                       text_art_inset=5, title24_y=2, title_y=4, meta_y=6, count_y=6)


def list_page(name):
    row = w('row', w('button', w('gif', kind='image', at=[0, 9, 50, 52]),
                     w('title', kind='hscroll_label', at=[0, 11, 197, 20]), w('meta', at=[0, 40, 197, 16]),
                     kind='button', at=[20, 0, 335, 70]), kind='list_item', at=[0, 0, 375, 78])
    return w(name, w('view_navbar', at=[0, 0, 375, 50]),
             w('list_view', row, kind='list_view', at=[0, 50, 375, 240], default_item_height='78'), kind='window')


@pytest.mark.parametrize('path, kind', [('localmusic/albuminfo_page.bin', 'text'), ('folder_page.bin', 'art')])
def test_patch_asset_rows_by_page_kind(monkeypatch, path, kind):
    # A text page's rows take pitch/body, an art page's art_pitch/art_body; both move their text to the
    # set's y. SET0 builds what it did.
    data = ipod.encode(list_page(path))
    monkeypatch.setitem(ipod.UI_ASSETS, path, ipod.sha(data))
    pitch, body, inset = (28, 26, 5) if kind == 'text' else (52, 50, 3)
    assert ipod.PAGE_KIND[path] == kind and ipod.page_rows(PAGE_T, kind) == (pitch, body, inset)
    n = named(ipod.decode(ipod.patch_asset(path, data, g=PAGE_T)))
    assert n['list_view'][2]['default_item_height'] == str(pitch) and n['row'][1][3] == pitch
    assert (n['button'][1][3], n['gif'][1][1], n['title'][1][1], n['meta'][1][1]) == (body, inset, 4, 6)
    n = named(ipod.decode(ipod.patch_asset(path, data)))
    assert n['list_view'][2]['default_item_height'] == '72' and (n['row'][1][3], n['button'][1][3]) == (72, 68)
    assert (n['gif'][1][1], n['title'][1][1], n['meta'][1][1]) == (8, 10, 39)


def test_songs_rows_are_their_own_kind():
    assert ipod.PAGE_KIND['localmusic/allmusic_page.bin'] == 'song'
    assert ipod.page_rows(dataclasses.replace(PAGE_T, song_pitch=52, song_body=50), 'song') == (52, 50, 5)


def test_text_y():
    assert [ipod.text_y(ipod.SET0, y) for y in (11, 14, 21, 40, 41)] == [10, 13, 20, 39, 40]
    assert [ipod.text_y(PAGE_T, y) for y in (11, 14, 40, 41)] == [4, 2, 6, 6]


SET0_WORDS = {'lib_body': 68, 'lib_inset': 8, 'pitch': 72, 'body': 68, 'art': 52, 'art_inset': 8, 'text_art': 52, 'text_art_inset': 8, 'scroll': 240,
              'title24_y': 13, 'title_y': 10, 'meta_y': 39, 'count_y': 40}
SPLIT = ('pitch', 'body', 'title24_y', 'title_y', 'meta_y', 'count_y')


def test_immediates_every_site_once():
    sites = [a for g in ipod.AUDIT['immediates'] for a, _ in g['sites']]
    assert len(sites) == 103 and len(set(sites)) == 103


def test_immediate_values_set0_are_the_baked_values():
    values = ipod.immediate_values(ipod.SET0)
    assert set(values) == set(ipod.VALUE_NAMES)
    assert (values['np_bar_x'], values['np_bar_w']) == (ipod.NP_BAR[0], ipod.NP_BAR[2])
    assert ipod.VALUE_NAMES[0] == 'pitch'
    for name in ipod.VALUE_NAMES:
        if not name.startswith(('np_bar', 'stock_', 'search_')):
            assert values[name] == SET0_WORDS[name if name in SET0_WORDS else name.removeprefix('art_').removeprefix('song_')], name


def test_only_split_names_take_the_art_prefix():
    assert {n for n in ipod.VALUE_NAMES if n.startswith('art_')} - {'art_inset'} <= {'art_' + n for n in SPLIT}
    assert {n for n in ipod.VALUE_NAMES if n.startswith('song_')} <= {'song_' + n for n in SPLIT}


def test_geometry_has_every_value_name():
    assert set(ipod.VALUE_NAMES) - {'scroll', 'np_bar_x', 'np_bar_w'} <= {f.name for f in dataclasses.fields(ipod.Geometry)}


TEXT_SITES = {'0x4b0f88': 'pitch', '0x4b0608': 'pitch', '0x4b0fc0': 'body', '0x4b1058': 'text_art',
              '0x4aa42c': 'text_art', '0x4adfec': 'text_art', '0x4aa4a4': 'text_art_inset', '0x4ae05c': 'text_art_inset'}


def test_localclass_sites_are_text():
    # Artists, Composers and Genres (localclass_page), and the artwork of Songs and an artist's songs, follow
    # the text rows, so a set can make them 28 px text while Albums keep 52 px art rows.
    got = {a: g['value'] for g in ipod.AUDIT['immediates'] for a, _ in g['sites'] if a in TEXT_SITES}
    assert got == TEXT_SITES and ipod.PAGE_KIND['localmusic/localclass_page.bin'] == 'text'


UI = 'release/assets/default/raw/ui/'


@pytest.fixture
def fake_assets(monkeypatch):
    monkeypatch.setattr(ipod, 'UI_ASSETS', {'system_bar.bin': '', 'dialog/volume_dialog.bin': '', 'home_page.bin': ''})
    monkeypatch.setattr(ipod, 'STOCK_ROWS', {})
    monkeypatch.setattr(ipod, 'patch_asset', lambda rel, data, g=ipod.SET0: data + (b'!' if g.bar != 30 and rel != 'home_page.bin' else b''))


def test_set_assets_only_what_differs(fake_assets):
    got = ipod.set_assets(lambda p: p.encode(), ipod.Geometry(name='test', bar=26))
    assert got == {UI + 'test_system_bar.bin': (UI + 'system_bar.bin').encode() + b'!',
                   UI + 'dialog/test_volume_dialog.bin': (UI + 'dialog/volume_dialog.bin').encode() + b'!'}


def test_set_assets_set0_is_empty(fake_assets):
    assert ipod.set_assets(lambda p: p.encode(), ipod.SET0) == {}


def test_classic_light_sets():
    # Classic: a 26 px bar, 32 px text rows, 52 px art rows with the art 1:1 (Row Art off: 32, no art),
    # and text pages without artwork.
    n, t = CLASSIC_LIGHT, CLASSIC_LIGHT_TEXT
    assert (n.bar, n.pitch, n.body, n.art_pitch, n.art_body, n.art, n.art_inset, n.text_art, n.text_art_inset) == (26, 32, 32, 52, 52, 52, 0, 0, 0)
    assert (n.song_pitch, n.song_body, n.song_title24_y, n.song_meta_y) == (52, 52, 1, 29) == (t.song_pitch, t.song_body, t.song_title24_y, t.song_meta_y)
    assert dataclasses.replace(t, name='classic-light', art=52, art_pitch=52, art_body=52, art_title24_y=1, art_meta_y=29, stream_row=52) == n
    assert (n.title24_y, n.title_y, n.meta_y, n.count_y, n.art_title24_y, n.art_meta_y, t.art_title24_y, t.art_meta_y) == (4, 6, 8, 8, 1, 29, 4, 8)
    assert (n.set_row, n.set_top, n.set_rows, n.set_icon) == (32, 0, 9, 0) == (t.set_row, t.set_top, t.set_rows, t.set_icon)
    assert ipod.immediate_values(n)['scroll'] == 262


# Library as a Home list: a hidden list holds an empty scroll view the Library page's constructor finds by name, as high as
# the window; the Library row's image is renamed, so stock's name match binds it nowhere and the payload does. The Carousel's
# card keeps stock's. A set without home_lib keeps stock's binding.
def test_home_library_list():
    root = home(); ipod.ipod_home(root, g=CLASSIC_LIGHT); rows = named(root[3][1])
    lib = root[3][3]
    assert lib[2]['name'] == 'list_view_homelib' and lib[2]['visible'] == 'false' and lib[1][3] == 294
    assert lib[3][0][2]['name'] == 'scroll_view_localmusic' and lib[3][0][3] == [] and lib[3][0][2]['yslidable'] == 'true'
    assert 'img_library' in rows and 'img_localmusic' not in rows
    assert 'img_localmusic' in named(root[3][5])  # the carousel's
    root = home(); ipod.ipod_home(root); rows = named(root[3][1])
    assert 'list_view_homelib' not in named(root) and 'img_library' not in rows and 'img_localmusic' in rows


# Streaming the same way: a second hidden list, its empty view named as the Streaming page's, the row's image renamed so stock
# binds it nowhere. The Carousel's card and a set without home_lib keep stock's binding.
def test_home_streaming_list():
    root = home(); ipod.ipod_home(root, g=CLASSIC_LIGHT); rows = named(root[3][1])
    stream = root[3][4]
    assert stream[2]['name'] == 'list_view_homestream' and stream[2]['visible'] == 'false' and stream[2]['item_height'] == '32' and stream[1][3] == 294
    assert stream[3][0][2]['name'] == 'scroll_view_streamsset' and stream[3][0][3] == [] and stream[3][0][2]['yslidable'] == 'true'
    assert 'img_services' in rows and 'img_stream' not in rows
    assert 'btn_stream' in named(root[3][5])  # the carousel's
    root = home(); ipod.ipod_home(root); rows = named(root[3][1])
    assert 'list_view_homestream' not in named(root) and 'img_services' not in rows and 'img_stream' in rows


# Songs' action bar under Classic: a row of the set's action_row, the list and the empty panel under it, and stock's recreated
# scroll bar spans that (the payload shapes what stock builds in it, navigation.c action_row). A set without action_row keeps
# stock's 50 px bar.
def test_songs_action_row(monkeypatch):
    path = 'localmusic/allmusic_page.bin'
    page = w('allmusic_page', w('view_navbar', at=[0, 0, 375, 50]), w('view_navbar_allplay', at=[0, 50, 375, 50]),
             w('view', at=[0, 100, 375, 193]), w('table_view', kind='table_view', at=[0, 100, 375, 190]), kind='window')
    data = ipod.encode(page); monkeypatch.setitem(ipod.UI_ASSETS, path, ipod.sha(data))
    n = named(ipod.decode(ipod.patch_asset(path, data, g=CLASSIC_LIGHT)))
    assert n['view_navbar_allplay'][1] == [0, 0, 375, 32] and n['table_view'][1] == [0, 32, 375, 262] and n['view'][1][1] == 32
    assert ipod.immediate_values(CLASSIC_LIGHT)['scroll'] == 262 and ipod.immediate_values(ipod.SET0)['scroll'] == 240
    n = named(ipod.decode(ipod.patch_asset(path, data)))
    assert n['view_navbar_allplay'][1] == [0, 0, 375, 50]


def test_settings_lists_follow_the_set(monkeypatch):
    # Settings lists hold the set's rows from its top: SET0 four 68 px rows from 8, Classic's whole window of 32 px rows from 0.
    path = 'systemset/sysset_page.bin'
    page = w('sysset_page', w('view_navbar', at=[0, 0, 375, 50]),
             w('list_view', kind='list_view', at=[0, 50, 375, 240], default_item_height='78'), kind='window')
    data = ipod.encode(page); monkeypatch.setitem(ipod.UI_ASSETS, path, ipod.sha(data))
    for g, box, row in ((ipod.SET0, [0, 8, 375, 272], '68'), (CLASSIC_LIGHT, [0, 0, 375, 294], '32')):
        n = named(ipod.decode(ipod.patch_asset(path, data, g=g)))
        assert n['list_view'][1] == box and n['list_view'][2]['default_item_height'] == row and 'item_height' not in n['list_view'][2]


# Stock's own list pages, each as its bin has it, under Classic: the rows of 78 take the approved pitch, by the one rule (stock_row), and the toolbars go. SET0 gets the bin back, byte for byte.
STOCK_ROW_REVERSED = dataclasses.replace(CLASSIC_LIGHT, stock_row=78, stock_body=70, stock_title_y=11, stock_title_h=20)


def stock_list_page(name, *rows):
    return w(name, w('view_navbar', at=[0, 0, 375, 50]),
             w('list_view', w('scroll_view', *rows, kind='scroll_view', at=[0, 0, 375, 100]), kind='list_view', at=[0, 50, 375, 210], default_item_height='78'), kind='window')


def stock_table_page(name, *more):
    return w(name, w('view_navbar', at=[0, 0, 375, 50]),
             w('table_view', w('table_client', kind='table_client', at=[0, 0, 365, 100], row_height='78', self_layout='default(x=0,y=0,w=365,h=100%)'), kind='table_view', at=[0, 50, 375, 190]), *more, kind='window')


def audited_rows(monkeypatch, path, page):
    data = ipod.encode(page); monkeypatch.setitem(ipod.STOCK_ROWS, path, ipod.sha(data))
    assert ipod.patch_asset(path, data) == data
    return data


@pytest.mark.parametrize('path', ['playermore_page.bin', 'updatemusic_page.bin'])
def test_stock_lists_take_settings_rows(monkeypatch, path):
    label = w('hscroll_label_1', kind='hscroll_label', at=[10, 25, 266, 20])
    row = w('list_item_1', w('button_1', label, kind='button', at=[20, 0, 335, 70]), kind='list_item', at=[0, 0, 0, 78])
    data = audited_rows(monkeypatch, path, stock_list_page(path[:-4], row))
    n = named(ipod.decode(ipod.patch_asset(path, data, g=CLASSIC_LIGHT)))
    assert n['view_navbar'][2]['visible'] == n['view_navbar'][2]['enable'] == 'false'
    assert n['list_view'][1] == [0, 0, 375, 294] and n['list_view'][2]['default_item_height'] == '32' and 'item_height' not in n['list_view'][2]
    assert n['hscroll_label_1'][1] == [10, 0, 266, 70]  # takes the row, whose font a 20 px box wouldn't hold
    assert 'anim_hint' not in ipod.decode(ipod.patch_asset(path, data, g=CLASSIC_LIGHT))[2]
    n = named(ipod.decode(ipod.patch_asset(path, data, g=STOCK_ROW_REVERSED)))  # the rule reversed: stock's pitch, the toolbar still hidden
    assert n['view_navbar'][2]['visible'] == 'false' and n['list_view'][2]['default_item_height'] == '78' and n['list_view'][1] == [0, 0, 375, 294]


def test_queue_under_the_bar(monkeypatch):
    path = 'playerqueue_page.bin'
    data = audited_rows(monkeypatch, path, stock_table_page('playerqueue_page'))
    n = named(ipod.decode(ipod.patch_asset(path, data, g=CLASSIC_LIGHT)))
    assert n['view_navbar'][2]['visible'] == 'false' and n['table_view'][1] == [0, 0, 375, 294]
    assert n['table_client'][2]['row_height'] == '32' and n['table_client'][1][2] == 375


@pytest.mark.parametrize('path', ['playerjumpinfo_page.bin', 'specfolder_page.bin'])
def test_stock_tables_keep_their_toolbar_and_boxes(monkeypatch, path):
    page = stock_table_page(path[:-4], w('startscan_view', at=[0, 210, 375, 60]))
    data = audited_rows(monkeypatch, path, page)
    n = named(ipod.decode(ipod.patch_asset(path, data, g=CLASSIC_LIGHT)))
    assert 'visible' not in n['view_navbar'][2] and n['table_view'][1] == [0, 50, 375, 190] and n['startscan_view'][1] == [0, 210, 375, 60]
    assert n['table_client'][2]['row_height'] == '32'
    assert ipod.patch_asset(path, data, g=STOCK_ROW_REVERSED) == ipod.encode(ipod.decode(data))


def test_toolbar_switch_pages(monkeypatch):
    # Lock Screen is a settings list, and the Sleep Timer's panel makes room for the row its switch becomes. Crossfade's page keeps its toolbar. SET0's keep theirs and their rows.
    lock, sleep, boot = 'systemset/screensaver_page.bin', 'systemset/sleepshutdown_page.bin', 'playset/bootvol_page.bin'
    pages = {lock: stock_list_page('screensaver_page'),
             sleep: w('sleepshutdown_page', w('view_navbar', at=[0, 0, 375, 50]), w('view_sleep', at=[0, 50, 375, 240]), kind='window'),
             boot: w('bootvol_page', w('view_navbar', at=[0, 0, 375, 50]), w('view_bootvol', at=[0, 50, 375, 240]),
                     w('slider', kind='slider', at=[50, 100, 275, 8]), w('a', kind='image', at=[0, 0, 50, 50], image='vol_left'), w('b', kind='image', at=[0, 0, 50, 50], image='vol_right'), kind='window')}
    for path, page in pages.items():
        monkeypatch.setitem(ipod.UI_ASSETS, path, ipod.sha(ipod.encode(page)))
    data = {path: ipod.encode(page) for path, page in pages.items()}
    n = named(ipod.decode(ipod.patch_asset(lock, data[lock], g=CLASSIC_LIGHT)))
    assert n['view_navbar'][2]['visible'] == 'false' and n['list_view'][1] == [0, 0, 375, 294] and n['list_view'][2]['default_item_height'] == '32'
    assert named(ipod.decode(ipod.patch_asset(lock, data[lock], g=STOCK_ROW_REVERSED)))['list_view'][2]['default_item_height'] == '78'
    n = named(ipod.decode(ipod.patch_asset(sleep, data[sleep], g=CLASSIC_LIGHT)))
    assert n['view_navbar'][2]['visible'] == 'false' and n['view_sleep'][1] == [0, 32, 375, 240] and 28 + 240 <= 294
    assert 'visible' not in named(ipod.decode(ipod.patch_asset(boot, data[boot], g=CLASSIC_LIGHT)))['view_navbar'][2]
    for path in (lock, sleep, boot):
        n = named(ipod.decode(ipod.patch_asset(path, data[path])))
        assert 'visible' not in n['view_navbar'][2] and n[{lock: 'list_view', sleep: 'view_sleep', boot: 'view_bootvol'}[path]][1][1] == 50
    assert named(ipod.decode(ipod.patch_asset(lock, data[lock])))['list_view'][2]['default_item_height'] == '78'


def test_search_rows_are_stocks_under_set0():
    values, want = ipod.immediate_values(ipod.SET0), {'search_row': 78, 'search_body': 70, 'search_art': 50, 'search_title_y': 11, 'search_title24_y': 14, 'search_title_h': 24, 'search_meta_y': 41, 'search_meta_h': 16}
    assert {n: values[n] for n in want} == want
    for group in ipod.AUDIT['immediates']:
        if group['value'] in want:
            assert all(int(word, 16) & 0xffff == want[group['value']] for _, word in group['sites']), group['value']
    assert (CLASSIC_LIGHT.search_row, CLASSIC_LIGHT.search_body, CLASSIC_LIGHT.search_art, CLASSIC_LIGHT.search_title_h, CLASSIC_LIGHT.search_meta_h) == (52, 52, 0, 28, 20)
    assert CLASSIC_LIGHT.search_title_y + CLASSIC_LIGHT.search_title_h <= CLASSIC_LIGHT.search_body and CLASSIC_LIGHT.search_meta_y + CLASSIC_LIGHT.search_meta_h <= CLASSIC_LIGHT.search_body


def test_stock_rows_are_stocks_under_set0():
    values = ipod.immediate_values(ipod.SET0)
    assert (values['stock_row'], values['stock_body'], values['stock_title_y'], values['stock_title_h']) == (78, 70, 11, 20) and ipod.SET0.toolbar == 1
    for group in ipod.AUDIT['immediates']:
        if group['value'] in ('stock_row', 'stock_body', 'stock_title_y', 'stock_title_h'):
            assert all(int(word, 16) & 0xffff == values[group['value']] for _, word in group['sites'])
    assert (CLASSIC_LIGHT.stock_row, CLASSIC_LIGHT.stock_body, CLASSIC_LIGHT.toolbar) == (32, 32, 0)
    assert CLASSIC_LIGHT.stock_title_h >= 27 and CLASSIC_LIGHT.stock_title_y + CLASSIC_LIGHT.stock_title_h <= CLASSIC_LIGHT.stock_body  # the box holds the row's font


def test_lists_beside_settings_rows_take_item_height(monkeypatch):
    # A library list with the set's settings row height is told apart by its item_height (Classic only).
    path = 'localmusic/albuminfo_page.bin'
    data = ipod.encode(list_page(path)); monkeypatch.setitem(ipod.UI_ASSETS, path, ipod.sha(data))
    assert named(ipod.decode(ipod.patch_asset(path, data, g=CLASSIC_LIGHT)))['list_view'][2]['item_height'] == '32'
    assert 'item_height' not in named(ipod.decode(ipod.patch_asset(path, data)))['list_view'][2]


def elf(size):
    """A little MIPS ELF: one LOAD segment, at 0x400000 from file offset 0x1000."""
    data = bytearray(0x1000 + size)
    struct.pack_into('<I', data, 28, 52)
    struct.pack_into('<HH', data, 42, 32, 1)
    struct.pack_into('<8I', data, 52, 1, 0x1000, 0x400000, 0x400000, size, size, 5, 0x1000)
    return data


SYMBOLS = {'compact_set_row_layout': 0x40a000, 'ipod_list_layout': 0x40b000, 'ringnav_boot': 0x40c000,
           'stock_set_row_layout': 0x40d000, 'stock_row_text': 0x40e000, 'stock_row_style': 0x40f000}
# SET0's word for each named immediate, as test_immediate_values_set0_are_the_baked_values reads SET0_WORDS.
# The stock_ and search_ rows' words are SET0's fields, as immediate_values gives them; that test leaves them out too.
IMMEDIATES = ipod.immediate_values(ipod.SET0) | {name: SET0_WORDS[name if name in SET0_WORDS else name.removeprefix('art_').removeprefix('song_')]
              for name in ipod.VALUE_NAMES if not name.startswith(('np_bar', 'stock_', 'search_'))} | {'np_bar_x': ipod.NP_BAR[0], 'np_bar_w': ipod.NP_BAR[2]}


def code_patches():
    """(address, stock word, patched word, purpose) for each word patch_code changes, in its order."""
    lookups = [(at + 4 * i, old, new, why) for at, slot in ((0x4a6250, 0x468), (0x4ad458, 0x468), (0x526e70, 0x420))
               for i, (old, new, why) in enumerate(((0x8f99aeb0, 0x8f99a8f8, 'lookup unwind uses navigator_close'),
                                                    (0x0320f809, 0x0320f809, 'lookup close call'),
                                                    (0, 0x8fa40000 | slot, 'lookup close receives the top window name')))]
    immediates = [(int(at, 16), int(old, 16), int(old, 16) & 0xffff0000 | (IMMEDIATES[v] if isinstance(v := group['value'], str) else v),
                   group['purpose']) for group in ipod.AUDIT['immediates'] for at, old in group['sites']]
    rows = ipod.AUDIT['row_layout_calls']
    tiles = [site for icon, style in ((0x4b2fa0, 0x4b3028), (0x4b30a4, 0x4b3130)) for site in (
        (icon, 0x24070032, 0x24070000, 'playlist tile icon width 0'),
        (icon + 0x54, 0x2407005b, 0x240700a4, 'playlist tile label spans the tile'),
        (icon + 0x60, 0x2405003f, 0x24050000, 'playlist tile label at the tile edge'),
        (style, 0x25058c04, 0x25059220, 'playlist tile label centred'))]
    # toolsTimeItoa's two formats, back to back
    times = zip(struct.unpack('<7I', b'%02d:%02d:%02d\0\0%02d:%02d\0\0\0'), struct.unpack('<7I', b'%d:%02d:%02d\0\0\0\0%d:%02d\0\0\0\0\0'))
    return [*lookups, *immediates, *[(int(at, 16), int(old, 16), 0x0c102800, rows['purpose']) for at, old in rows['sites']],
            *[(int(at, 16), int(old, 16), jal, ipod.AUDIT[name]['purpose']) for name, jal in (('stock_row_layout_calls', 0x0c103400), ('stock_row_text_calls', 0x0c103800),
                                                                                         ('stock_row_style_calls', 0x0c103c00)) for at, old in ipod.AUDIT[name]['sites']],
            (0x522410, 0x0320f809, 0, 'folder rebind retains the width owned by its row layouter'), *tiles,
            (ipod.inc('LIST_VIEW_LAYOUT_SLOT'), ipod.inc('LIST_VIEW_LAYOUT'), 0x40b000, 'settings lists stack SET_ROW rows and map their children'),
            *[(0x7778a0 + 4 * i, old, new, 'times without a leading zero') for i, (old, new) in enumerate(times)],
            (0x523de0, 0x0320f809, 0x0c103000, 'boot resume restores the queue paused and stays on Home')]


def test_patch_code():
    patches = code_patches()
    assert len({at for at, *_ in patches}) == len(patches)
    data = elf(0x530000)
    for at, old, _, _ in patches:
        struct.pack_into('<I', data, at - 0x3ff000, old)
    assert ipod.patch_code(data, dict(SYMBOLS)) == [{'address': hex(at), 'original': hex(old), 'patched': hex(new), 'purpose': why}
                                                     for at, old, new, why in patches]
    assert [struct.unpack_from('<I', data, at - 0x3ff000)[0] for at, *_ in patches] == [new for _, _, new, _ in patches]


def test_classic_times_fit():
    """Classic's times: each length class's widest remaining time fits its box at NP_IPOD_TIME_PX, in the model
    status_metrics uses (stb scales a font so that ascent - descent is its size; advances, no kerning), every
    digit tried; NP_IPOD_TIME_PX is the largest whose line fits the box's height, NP_IPOD_TIME_H."""
    from fontTools.ttLib import TTFont
    f = TTFont(ipod.CLASSIC_LIGHT_FONT_SRC, lazy=True)
    asc, desc, cmap = f['hhea'].ascent, f['hhea'].descent, f.getBestCmap()
    width = lambda text, px: math.ceil(sum(f['hmtx'][cmap[ord(c)]][0] for c in text) * px / (asc - desc))
    px, box = ipod.NP_IPOD_TIME_PX, ipod.inc('NP_IPOD_TIME_H')
    assert (px, ipod.NP_IPOD_TIME_WS) == (21, [35, 43, 56]) == (px, ipod.time_widths(ipod.CLASSIC_LIGHT_FONT_SRC))
    for w, longest in zip(ipod.NP_IPOD_TIME_WS, ('9:59', '59:59', '9:59:59')):
        for d in '0123456789':
            assert width('-' + re.sub(r'\d', d, longest), px) <= w and width(re.sub(r'\d', d, longest), px) <= w
    assert width('-1:02:03', px) == ipod.NP_IPOD_TIME_W and px <= box < px + 1


def test_classic_time_classes():
    """The bar between the fitted boxes (navigation.c ipod_bar_x): the boxes' outer edges and the gap kept, centred;
    the build's the widest, the under-10:00 class's, so a long track's only narrows it."""
    edge = ipod.inc('NP_IPOD_BAR_X') - ipod.NP_IPOD_TIME_GAP - ipod.NP_IPOD_TIME_WS[0]
    bars = [(edge + w + ipod.NP_IPOD_TIME_GAP, 375 - 2 * (edge + w + ipod.NP_IPOD_TIME_GAP)) for w in ipod.NP_IPOD_TIME_WS]
    assert edge == 23 and bars == [(66, 243), (74, 227), (87, 201)] and bars[0][0] == ipod.with_geometry(CLASSIC_LIGHT).NP_BAR_X


# Artist detail: stock's tabs carry literal Chinese text in every language; they take the string table's keys,
# and the Albums tab and its page (the pages' second) start active.
def artist_page():
    tabs = w('view_tabs', w('btn_track', kind='tab_button', text='单曲', style='s_tab'), w('btn_album', kind='tab_button', text='专辑'))
    return w('artistinfo_page', tabs, w('pages', w('view_songs'), w('view_albums'), kind='pages'))


def test_artist_tabs():
    root = artist_page()
    ipod.artist_tabs(root)
    n = named(root)
    assert n['btn_track'][2] == {'name': 'btn_track', 'tr_text': 'local_allsongs', 'style': 's_tab'}
    assert n['btn_album'][2] == {'name': 'btn_album', 'tr_text': 'album', 'value': 'true'}
    assert n['pages'][2] == {'name': 'pages', 'value': '1'} and n['pages'][3][1][2]['name'] == 'view_albums'
    assert n['view_tabs'][2] == {'name': 'view_tabs'} and n['view_albums'][2] == {'name': 'view_albums'}


@pytest.mark.parametrize('change,message', [
    (lambda n: n['btn_track'].__setitem__(0, 'button'), 'btn_track: unexpected tab'),
    (lambda n: n['btn_album'][2].update(text='Albums'), 'btn_album: unexpected tab'),
    (lambda n: n['pages'][2].update(value='0'), 'Unexpected artist pages'),
    (lambda n: n['pages'].__setitem__(0, 'view'), 'Unexpected artist tabs'),
    (lambda n: n['artistinfo_page'][3].pop(), 'Unexpected artist tabs'),
    (lambda n: n['view_tabs'][3].pop(0), 'Unexpected artist tabs'),
    (lambda n: n['view_tabs'][3].append(copy.deepcopy(n['btn_album'])), 'Unexpected artist tabs'),
])
def test_artist_tabs_wants_stock(change, message):
    root = artist_page()
    change(named(root))
    with pytest.raises(ValueError, match=f'^{message}$'):
        ipod.artist_tabs(root)


def volume():
    return w('volume_dialog', w('slider_vol', kind='slider', value='30'), w('label_vol', kind='label', text='30'),
             kind='dialog', highlight='default(alpha=200)', anim_hint='popup')


def test_volume_dialog():
    # No highlighter, so nothing under the dialog dims; stock's controls hidden from the first paint, the payload draws the volume.
    root = volume()
    ipod.volume_dialog(root)
    assert root[2] == {'name': 'volume_dialog', 'anim_hint': 'popup'}
    assert [c[2] for c in root[3]] == [{'name': 'slider_vol', 'value': '30', 'visible': 'false'}, {'name': 'label_vol', 'text': '30', 'visible': 'false'}]
    assert [c[:2] for c in root[3]] == [c[:2] for c in volume()[3]]


@pytest.mark.parametrize('change,message', [
    (lambda r: r.__setitem__(0, 'window'), 'Unexpected volume dialog'),
    (lambda r: r[2].update(name='x'), 'Unexpected volume dialog'),
    (lambda r: r[2].update(highlight='default(alpha=100)'), 'Unexpected volume dialog'),
    (lambda r: r[2].pop('highlight'), 'Unexpected volume dialog'),
    (lambda r: r[3].reverse(), 'Unexpected volume controls'),
    (lambda r: r[3][0].__setitem__(0, 'image'), 'Unexpected volume controls'),
    (lambda r: r[3][1].__setitem__(0, 'hscroll_label'), 'Unexpected volume controls'),
    (lambda r: r[3][1][2].update(name='x'), 'Unexpected volume controls'),
    (lambda r: r[3].append(w('img_vol', kind='image')), 'Unexpected volume controls'),
])
def test_volume_dialog_wants_stock(change, message):
    root = volume()
    change(root)
    with pytest.raises(ValueError, match=f'^{message}$'):
        ipod.volume_dialog(root)


def style_file(entries):
    """An AWTK style file: magic, version and count; a 100-byte entry per (widget, style, state), the offset of its
    properties, then its state, style and widget names in 32 bytes each; at that offset the properties' count, then
    each one's type, key length (its NUL counted), value size, key and value."""
    table, blocks, start = b'', b'', 12 + 100 * len(entries)
    for (widget, style, state), props in entries.items():
        table += struct.pack('<I32s32s32s', start + len(blocks), state.encode(), style.encode(), widget.encode())
        blocks += struct.pack('<I', len(props))
        for key, value in props.items():
            blocks += struct.pack('<BBH', 1, len(key) + 1, len(value)) + key.encode() + b'\0' + value
    return struct.pack('<3I', 0xfafbfcfd, 1, len(entries)) + table + blocks


def test_style_props():
    entries = {('button', 's_btn_listitem', 'normal'): {'bg_color': bytes.fromhex('2b2b2bff'), 'font_size': struct.pack('<I', 20)},
               ('label', 'default', 'focused'): {'text_color': bytes.fromhex('ffffffff')}, ('view', 'empty', 'normal'): {}}
    data = style_file(entries)
    got = list(ipod.style_props(data))
    assert [g[:4] + g[5:] for g in got] == [(*entry, key, value) for entry, props in entries.items() for key, value in props.items()]
    for *_, key, at, value in got:  # each offset its value's own bytes, after its key
        assert data[at - len(key) - 1:at + len(value)] == key.encode() + b'\0' + value


def test_style_props_wants_the_magic():
    data = bytearray(style_file({('button', 'default', 'normal'): {'bg_color': bytes(4)}}))
    data[0] ^= 1
    with pytest.raises(ValueError, match='^Unexpected AWTK style magic$'):
        list(ipod.style_props(bytes(data)))


GREY, RED, CLEAR, PRESSED = (bytes.fromhex(c) for c in ('2b2b2bff', '3d1920ff', '00000000', '333333ff'))
CLEAR_ROWS = ('button', 's_btn_listitem', 'bg_color', '2b2b2bff', '00000000', 2)


def list_styles(normal=GREY, pressed=RED, focused=GREY):
    # The edited style's bg_color in three states; the same value under another property, style or widget stays.
    return style_file({('button', 's_btn_listitem', 'normal'): {'bg_color': normal, 'text_color': GREY},
                       ('button', 's_btn_listitem', 'pressed'): {'bg_color': pressed},
                       ('button', 's_btn_listitem', 'focused'): {'bg_color': focused},
                       ('button', 'default', 'normal'): {'bg_color': GREY},
                       ('label', 's_btn_listitem', 'normal'): {'bg_color': GREY}})


def style_audit(*edits):
    return {'sha256': ipod.sha(list_styles()), 'edits': [list(e) for e in edits]}


def test_patch_style():
    assert ipod.patch_style(list_styles(), style_audit(CLEAR_ROWS)) == list_styles(normal=CLEAR, focused=CLEAR)
    pressed = ('button', 's_btn_listitem', 'bg_color', '3d1920ff', '333333ff', 1)
    assert ipod.patch_style(list_styles(), style_audit(CLEAR_ROWS, pressed)) == list_styles(CLEAR, PRESSED, CLEAR)


@pytest.mark.parametrize('edit,message', [
    (CLEAR_ROWS[:4] + ('000000', 2), 's_btn_listitem.bg_color: edit changes size'),
    (CLEAR_ROWS[:5] + (3,), 's_btn_listitem.bg_color: expected 3 states with 2b2b2bff, found 2'),
    (CLEAR_ROWS[:5] + (1,), 's_btn_listitem.bg_color: expected 1 states with 2b2b2bff, found 2'),
    (('button', 's_btn_listitem', 'bg_color', 'ffffffff', '00000000', 1), 's_btn_listitem.bg_color: expected 1 states with ffffffff, found 0'),
    (('button', 's_btn_listitem', 'border_color', '2b2b2bff', '00000000', 1), 's_btn_listitem.border_color: expected 1 states with 2b2b2bff, found 0'),
])
def test_patch_style_wants_each_edit_as_audited(edit, message):
    with pytest.raises(ValueError, match=f'^{message}$'):
        ipod.patch_style(list_styles(), style_audit(edit))


def test_patch_style_is_audited():
    with pytest.raises(ValueError, match='^Unaudited style file$'):
        ipod.patch_style(list_styles(pressed=GREY), style_audit(CLEAR_ROWS))


def png(width, height, depth=8, colour=6):
    """A whole PNG, every sample 0: the signature, IHDR, one IDAT and IEND."""
    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))
    row = 1 + (width * {0: 1, 2: 3, 4: 2, 6: 4}[colour] * depth + 7) // 8  # a filter byte, then the samples
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, depth, colour, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(bytes(row * height))) + chunk(b'IEND', b''))


@pytest.mark.parametrize('header', [(52, 52, 8, 6), (40, 3, 8, 2), (1, 7, 16, 0), (300, 2, 16, 4)])
def test_png_header(header):
    assert ipod.png_header(png(*header)) == header


@pytest.mark.parametrize('data', [b'', b'\xff\xd8\xff\xe0' + bytes(30), b'\x89PNG\r\n\x1a\r' + png(4, 4)[8:],
                                  png(4, 4)[:12] + b'IDAT' + png(4, 4)[16:]])  # IHDR must come first
def test_png_header_wants_a_png(data):
    with pytest.raises(ValueError, match='^Not a PNG$'):
        ipod.png_header(data)


@pytest.mark.parametrize('found,tool', [({'magick': '/usr/bin/magick', 'convert': '/usr/bin/convert'}, '/usr/bin/magick'),
                                        ({'convert': '/usr/bin/convert'}, '/usr/bin/convert')])
def test_imagemagick_runs_magick_or_convert(monkeypatch, found, tool):
    # ImageMagick 7's magick, else 6's convert: the arguments after it, the input on stdin, its stdout the result, a failure raised.
    runs = []
    monkeypatch.setattr(shutil, 'which', lambda name: found.get(name))
    monkeypatch.setattr(subprocess, 'run', lambda args, **kw: runs.append((args, kw)) or subprocess.CompletedProcess(args, 0, stdout=b'out'))
    assert ipod.imagemagick('png:-', '-trim', 'png:-', data=b'in') == b'out'
    (args, kw), = runs
    assert args == [tool, 'png:-', '-trim', 'png:-'] and kw == {'input': b'in', 'stdout': subprocess.PIPE, 'check': True}


def test_imagemagick_is_required(monkeypatch):
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kw: pytest.fail('ran without ImageMagick'))
    with pytest.raises(ValueError, match=r'^ImageMagick \(magick or convert\) is required for the iPod settings icons$'):
        ipod.imagemagick('-version', data=b'')


class Magick:
    """Stands in for ipod.imagemagick: keeps each call's arguments and input, and answers reply(args, data)."""
    def __init__(self, reply=lambda args, data: b'png'):
        self.calls, self.reply = [], reply

    def __call__(self, *args, data):
        self.calls.append((args, data))
        return self.reply(args, data)


@pytest.fixture
def magick(monkeypatch):
    m = Magick()
    monkeypatch.setattr(ipod, 'imagemagick', m)
    return m


def status_drawn(monkeypatch, magick, name):
    """name's icon made from its audited stock bytes: its -draw script, ImageMagick's arguments and input."""
    stock = b'stock ' + name.encode()
    monkeypatch.setitem(ipod.STATUS_ICONS, name, ipod.sha(stock))
    assert ipod.status_icon(name, stock) == b'png'
    args, data = magick.calls.pop()
    return args[args.index('-draw') + 1], args, data


def icon_size(args):
    """The width and height the icon is shrunk to."""
    width, height = args[args.index('-resize') + 1].removesuffix('!').split('x')
    return int(width), int(height)


@pytest.mark.parametrize('name', ['bar_wifi0.png', 'bar_wifi4.png', 'bar_bt.png', 'bar_btcon.png', 'bar_play.png', 'bar_pause.png'])
def test_status_icon_drawn_at_4x(monkeypatch, magick, name):
    # Drawn on a clear canvas four times the bar's 16 px icon, then shrunk to it: nothing of stock's image is kept.
    draw, args, data = status_drawn(monkeypatch, magick, name)
    width, height = icon_size(args)
    assert height == 16 and args[:3] == ('-size', f'{4 * width}x{4 * height}', 'xc:none') and draw.startswith('scale 4,4 ') and data == b''
    assert args[-len(ipod.PNG_OUT):] == tuple(ipod.PNG_OUT)


def wifi_inks(monkeypatch, magick, level):
    """The dot's colour, then its three arcs' from the innermost out."""
    draw, _, _ = status_drawn(monkeypatch, magick, f'bar_wifi{level}.png')
    arcs = sorted((float(r), colour) for colour, r in re.findall(r"stroke '([^']+)' path 'M[\d.,]+ A([\d.]+)", draw))
    dot, = re.findall(r"fill '([^']+)' circle", draw)
    return [dot] + [colour for _, colour in arcs]


@pytest.mark.parametrize('level', range(5))
def test_status_icon_wifi_lights_from_the_dot_out(monkeypatch, magick, level):
    # A signal level lights that many of the dot and its three arcs, innermost first; the rest are unlit.
    (lit,), (unlit,) = set(wifi_inks(monkeypatch, magick, 4)), set(wifi_inks(monkeypatch, magick, 0))
    assert lit != unlit and wifi_inks(monkeypatch, magick, level) == [lit] * level + [unlit] * (4 - level)


def test_status_icon_bluetooth(monkeypatch, magick):
    # Lit as Wi-Fi's ink when connected, unlit when not; its ink, round caps and all, in the canvas's last BT_REACH
    # columns, as stock's.
    (lit,), (unlit,) = set(wifi_inks(monkeypatch, magick, 4)), set(wifi_inks(monkeypatch, magick, 0))
    for name, colour in (('bar_btcon.png', lit), ('bar_bt.png', unlit)):
        draw, args, _ = status_drawn(monkeypatch, magick, name)
        (stroke, path), = re.findall(r"stroke '([^']+)' path '([^']+)'", draw)
        (width,) = re.findall(r'stroke-width ([\d.]+)', draw)
        xs, half, canvas = [float(x) for x in re.findall(r'[ML]([\d.]+),', path)], float(width) / 2, icon_size(args)[0]
        assert stroke == colour and canvas - ipod.BT_REACH <= min(xs) - half and max(xs) + half <= canvas, name


def test_status_icon_play_and_pause(monkeypatch, magick):
    play, _, _ = status_drawn(monkeypatch, magick, 'bar_play.png')
    (path,) = re.findall(r"path '([^']+)'", play)
    assert 'fill white' in play and len(re.findall(r'[ML]', path)) == 3 and path.endswith('Z')  # a filled triangle
    pause, _, _ = status_drawn(monkeypatch, magick, 'bar_pause.png')
    bars = re.findall(r'M([\d.]+),([\d.]+) L([\d.]+),([\d.]+)', pause)
    assert len(bars) == 2 and all(x0 == x1 and y0 != y1 for x0, y0, x1, y1 in bars)  # two upright bars


@pytest.mark.parametrize('name,data', [('bar_wifi2.png', b'other'), ('bar_other.png', b'stock')])
def test_status_icon_is_audited(monkeypatch, magick, name, data):
    monkeypatch.setitem(ipod.STATUS_ICONS, 'bar_wifi2.png', ipod.sha(b'stock'))
    with pytest.raises(ValueError, match=f'^{name}: unaudited status icon$'):
        ipod.status_icon(name, data)
    assert magick.calls == []


def test_row_icon(magick):
    assert ipod.row_icon(b'in') == b'png' and ipod.row_icon(b'in', 48, sharp=True) == b'png'
    head = ('png:-', '-alpha', 'on', '-filter', 'Lanczos', '-resize')
    (plain, _), (sharp, data) = magick.calls
    assert plain == (*head, f'{ipod.SET_ICON}x{ipod.SET_ICON}!', *ipod.PNG_OUT) and data == b'in'
    assert sharp[:len(head) + 1] == (*head, '48x48!') and sharp[len(head) + 1] == '-unsharp' and sharp[-len(ipod.PNG_OUT):] == tuple(ipod.PNG_OUT)  # sharpened after the shrink


STOCK_ICON, SMALL_ICON = png(ipod.SET_STOCK_ICON, ipod.SET_STOCK_ICON), png(ipod.SET_ICON, ipod.SET_ICON)


def settings_magick(monkeypatch, stock=STOCK_ICON, out=SMALL_ICON, opaque=(b'False', b'False')):
    """playset_eq.png audited as stock; ImageMagick's resize gives out, its %[opaque] opaque[0] for stock and opaque[1] for out."""
    monkeypatch.setitem(ipod.SETTINGS_ICONS, 'playset_eq.png', ipod.sha(stock))
    m = Magick(lambda args, data: (opaque[0] if data == stock else opaque[1]) if '%[opaque]' in args else out)
    monkeypatch.setattr(ipod, 'imagemagick', m)
    return m


def test_settings_icon(monkeypatch):
    m = settings_magick(monkeypatch)
    assert ipod.settings_icon('playset_eq.png', STOCK_ICON) == SMALL_ICON
    (resize, data), *opaque = m.calls
    assert data == STOCK_ICON and f'{ipod.SET_ICON}x{ipod.SET_ICON}!' in resize  # row_icon's, to SET_ICON
    assert [d for _, d in opaque] == [STOCK_ICON, SMALL_ICON]  # the transparency, before and after


@pytest.mark.parametrize('name,data', [('playset_eq.png', STOCK_ICON + b'\0'), ('other.png', STOCK_ICON)])
def test_settings_icon_is_audited(monkeypatch, name, data):
    m = settings_magick(monkeypatch)
    with pytest.raises(ValueError, match=f'^{name}: unaudited settings icon$'):
        ipod.settings_icon(name, data)
    assert m.calls == []


@pytest.mark.parametrize('stock', [png(ipod.SET_STOCK_ICON, ipod.SET_STOCK_ICON, colour=2), png(ipod.SET_STOCK_ICON, ipod.SET_STOCK_ICON + 1),
                                   png(ipod.SET_STOCK_ICON - 1, ipod.SET_STOCK_ICON), png(ipod.SET_STOCK_ICON, ipod.SET_STOCK_ICON, depth=16)])
def test_settings_icon_wants_stock_rgba(monkeypatch, stock):
    m = settings_magick(monkeypatch, stock=stock)
    with pytest.raises(ValueError, match='^playset_eq.png: unexpected stock icon format$'):
        ipod.settings_icon('playset_eq.png', stock)
    assert m.calls == []


@pytest.mark.parametrize('out', [png(ipod.SET_ICON + 1, ipod.SET_ICON), png(ipod.SET_ICON, ipod.SET_ICON, colour=4),
                                 png(ipod.SET_ICON, ipod.SET_ICON, colour=2), png(ipod.SET_ICON, ipod.SET_ICON, depth=16)])
def test_settings_icon_wants_set_icon_rgba(monkeypatch, out):
    settings_magick(monkeypatch, out=out)
    with pytest.raises(ValueError, match=f'^playset_eq.png: filtered icon is not {ipod.SET_ICON}px RGBA$'):
        ipod.settings_icon('playset_eq.png', STOCK_ICON)


def test_settings_icon_keeps_the_transparency(monkeypatch):
    settings_magick(monkeypatch, opaque=(b'False', b'True'))
    with pytest.raises(ValueError, match='^playset_eq.png: filtering changed the transparency$'):
        ipod.settings_icon('playset_eq.png', STOCK_ICON)


def test_quiet_icon(monkeypatch, magick):
    # Stock's control icons trimmed, shrunk into a square, one flat grey (More's pressed state darker), centred on a
    # larger square.
    fills = {}
    for name in ('play_more.png', 'play_fav.png', 'play_moredown.png'):
        stock = b'stock ' + name.encode()
        monkeypatch.setitem(ipod.QUIET_ICONS, name, ipod.sha(stock))
        assert ipod.quiet_icon(name, stock) == b'png'
        (args, data), = magick.calls; magick.calls.clear()
        (w, h), (ew, eh) = (map(int, args[args.index(k) + 1].split('x')) for k in ('-resize', '-extent'))
        assert data == stock and w == h and ew == eh > w, name
        assert args.index('-trim') < args.index('-resize') < args.index('-extent') and args[-len(ipod.PNG_OUT):] == tuple(ipod.PNG_OUT)
        assert args[args.index('-colorize') + 1] == '100' and args[args.index('-gravity') + 1] == 'center', name
        assert args[args.index('-background') + 1] == 'none', name  # on a clear square
        fills[name] = args[args.index('-fill') + 1]
    grey = lambda c: int(c.lstrip('#')[:2], 16)
    assert fills['play_more.png'] == fills['play_fav.png'] and grey(fills['play_moredown.png']) < grey(fills['play_more.png'])


def test_quiet_icon_is_audited(monkeypatch, magick):
    monkeypatch.setitem(ipod.QUIET_ICONS, 'play_fav.png', ipod.sha(b'stock'))
    with pytest.raises(ValueError, match='^play_fav.png: unaudited control icon$'):
        ipod.quiet_icon('play_fav.png', b'other')
    assert magick.calls == []


# MIPS branches count words from the delay slot, at + 4, in a signed 16-bit offset.
@pytest.mark.parametrize('op,at,target,word', [
    (0x10000000, 0x400000, 0x400004, 0x10000000),
    (0x10000000, 0x400000, 0x400000, 0x1000ffff),  # b . (a branch to itself)
    (0x10000000, 0x400000, 0x400010, 0x10000003),
    (0x04110000, 0x400010, 0x400000, 0x0411fffb),
    (0x10000000, 0x400000, 0x400004 + 4 * 32767, 0x10007fff),
    (0x04110000, 0x400000, 0x400004 - 4 * 32768, 0x04118000),
])
def test_branch(op, at, target, word):
    assert ipod.branch(op, at, target) == word


@pytest.mark.parametrize('target', [0x400004 + 4 * 32768, 0x400004 - 4 * 32769])
def test_branch_out_of_range(target):
    with pytest.raises(ValueError, match='^0x400000: branch out of range$'):
        ipod.branch(0x10000000, 0x400000, target)


def test_artist_albums_branch():
    # The artist page's init: stock's bal builds the Songs view (0x4adcbc); the patch's calls the Albums tab handler (0x4ac7ec).
    at, stock, patched = ipod.ARTIST_ALBUMS[1]
    assert (stock, patched) == (ipod.branch(0x04110000, at, 0x4adcbc), ipod.branch(0x04110000, at, 0x4ac7ec))


def program(*words, base=0x400000):
    """A MIPS ELF with one LOAD segment: words at base, from file offset 0x100."""
    data = bytearray(0x100) + struct.pack(f'<{len(words)}I', *words)
    struct.pack_into('<I', data, 28, 52)  # e_phoff
    struct.pack_into('<HH', data, 42, 32, 1)  # e_phentsize, e_phnum
    struct.pack_into('<8I', data, 52, 1, 0x100, base, base, 4 * len(words), 4 * len(words), 5, 0x1000)
    return data


def test_patch_word():
    data, changes = program(0x0320f809, 0x0411fc39, 0), []
    ipod.patch_word(data, 0x400004, 0x0411fc39, 0x0411f705, 'artist detail opens on Albums', changes)
    assert data == program(0x0320f809, 0x0411f705, 0)
    assert changes == [{'address': '0x400004', 'original': '0x411fc39', 'patched': '0x411f705', 'purpose': 'artist detail opens on Albums'}]
    ipod.patch_word(data, 0x400008, 0, 0x27bdffe0, 'not recorded')
    assert data == program(0x0320f809, 0x0411f705, 0x27bdffe0) and len(changes) == 1


def test_patch_word_wants_the_stock_instruction():
    data, changes = program(0x0320f809, 0x0411fc39), []
    with pytest.raises(ValueError, match='^0x400004: unexpected instruction$'):
        ipod.patch_word(data, 0x400004, 0x0320f809, 0, 'x', changes)
    assert data == program(0x0320f809, 0x0411fc39) and changes == []
