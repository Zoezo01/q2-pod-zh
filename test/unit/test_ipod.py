"""tools/ipod.py's page rewrites on small synthetic UI trees built to the shape each one checks for."""
import copy
import dataclasses

import pytest

import ipod
import theme

CLASSIC_LIGHT, CLASSIC_LIGHT_TEXT = (theme.sets(theme.builtins()[1])[s] for s in ('classic-light', 'classic-light-text'))  # Classic's sets: its theme.xml


def w(name, *children, kind='view', at=None, **props):
    return [kind, at or [0, 0, 10, 10], {'name': name, **props}, list(children)]


def named(root):
    return {n[2].get('name'): n for n in ipod.walk(root)}


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


def test_classic_light_bar_room_stays_the_gates(monkeypatch):
    ipod.status_bar(bar(), g=CLASSIC_LIGHT)  # 97, wider than BATT_ROOM's 94 the payload's gate reads: safe
    monkeypatch.setattr(ipod, 'BATT_ROOM', 99)
    with pytest.raises(ValueError, match=r"^BATT_ROOM \d+ past the bar's room \d+$"):
        ipod.status_bar(bar(), g=CLASSIC_LIGHT)


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
    slider = ['slider', [0, 0, 1, 1], dict(SLIDER), []]
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
    hold = str(ipod.inc('MARQUEE_HOLD_MS'))
    assert title['delay'] == title['lull'] == artist['delay'] == artist['lull'] == hold
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
    hold = str(ipod.inc('MARQUEE_HOLD_MS'))
    for name in ('scrlabel_title', 'scrlabel_artist'):
        assert n[name][2]['delay'] == n[name][2]['lull'] == hold
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


def test_slider_plain_wants_two_endpoints():
    with pytest.raises(ValueError, match=r'^x: unexpected slider endpoints$'):
        ipod.SETTINGS_SLIDERS, saved = ('x',), ipod.SETTINGS_SLIDERS
        try:
            ipod.slider_plain('x', settings_page('vol_left'))
        finally:
            ipod.SETTINGS_SLIDERS = saved


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
def test_status_metrics_centre_the_caps_in_the_classic_light_bar():
    g = CLASSIC_LIGHT
    m = ipod.status_metrics(g, ipod.CLASSIC_LIGHT_FONT_SRC)
    assert m['px'] == g.bar_px == 21 and m['cap'] == 11 and m['cap_top'] == 6 and m['label_y'] == -1
    assert m['inset'] == ipod.corner_inset(6) + ipod.BAR_PAD == 56  # BAR_PAD clear of the glass at the caps' top
    assert m['clock_text'] == 64 and classic_light_text_w('12:59 PM') <= 64  # its numbers only: Minimal's and Classic's keep CLOCK_TEXT
    assert ipod.CLOCK_TEXT == 70
    assert m['title'] == [54, -1, 98, g.bar - 1]  # text on columns 56 to 150; a 12-hour clock's starts at 155
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
    assert 375 - ipod.corner_inset(4) - 1 - (314 - ipod.inc('BATT_NUB_W')) == ipod.BAR_PAD  # the body's top-right pixel, row 4, BAR_PAD clear of the glass; the nub's rows lower still
    assert [c[2]['name'] for c in root[3] if c[1][0] == -200] == ipod.STATUS_HIDDEN  # only these parked


# EQ floats inside the room the battery's reach counts, not left of img_bt's 43 px canvas (whose ink is its last
# BT_REACH px), where the layout would put it: past the clock's text. In the bar's face, on its text line.
def test_classic_light_status_bar_eq_in_the_room():
    root = bar(); ipod.status_bar(root, g=CLASSIC_LIGHT); n = named(root)
    m, eq = ipod.status_metrics(CLASSIC_LIGHT, ipod.CLASSIC_LIGHT_FONT_SRC), n['label_eq']
    x, gap = n['view_right'][1][0] + eq[1][0], ipod.inc('STATUS_GAP')
    edge = 315  # the group's right edge: the nub ends at x 314
    assert eq[2]['floating'] == 'true' and eq[1][1:] == [m['title'][1], 28, m['title'][3]]
    assert x == 222 and x >= 375 / 2 + m['clock_text'] / 2 + ipod.CLASSIC_LIGHT_CLOCK_GAP  # the widest clock, CLASSIC_LIGHT_CLOCK_GAP clear
    assert classic_light_text_w('EQ') <= 28  # the bar's face fits its box
    # 2 px short of Bluetooth's ink: the battery with its soft edge and nub, Wi-Fi, BT_REACH
    assert x + 28 + 2 <= edge - (1 + CLASSIC_LIGHT.batt_w + ipod.inc('BATT_NUB_W')) - gap - n['img_wifi'][1][2] - gap - ipod.BT_REACH
    assert {eq[2][f'style:{s}:{k}'] for s in ('normal', 'disable', 'focused') for k in ('font_size', 'font_name')} \
        == {str(CLASSIC_LIGHT.bar_px), ipod.CLASSIC_LIGHT_FONT_NAME}


# Where the row layout puts the icons: right-aligned from the group's margin, the slot 31 (its soft edge, the body,
# the nub), hidden children taking no room. Bluetooth's and Wi-Fi's canvases are stock's; their ink lands on the
# columns and rows Classic's look states, and with the play state hidden they close up on the battery.
def test_classic_light_status_bar_icons_on_their_columns():
    root = bar(); ipod.status_bar(root, g=CLASSIC_LIGHT); n = named(root)
    view = n['view_right']
    assert view[1][1::2] == [0, 22]  # the 16 px canvases at y 3; the payload moves Bluetooth a row down, Wi-Fi a row up (bar.icons)
    assert n['img_state'][2].get('floating') is None
    def lay(*names):
        x, out = 315, {}
        for name in reversed(names):
            w = 31 if name == 'view_battery' else n[name][1][2]
            x -= w
            out[name] = (x, x + w)
            x -= ipod.inc('STATUS_GAP')
        return out
    full = lay('img_bt', 'img_wifi', 'img_state', 'view_battery')
    assert full['img_bt'][1] == 240 and {k: v for k, v in full.items() if k != 'img_bt'} == {
        'view_battery': (284, 315), 'img_state': (262, 280), 'img_wifi': (244, 258)}
    assert full['img_bt'][1] - ipod.BT_REACH == 230  # Bluetooth's ink
    bare = lay('img_bt', 'img_wifi', 'view_battery')
    assert bare['img_wifi'] == (266, 280) and bare['img_bt'][1] - ipod.BT_REACH == 252
    assert n['label_eq'][1][0] + view[1][0] + 28 < 252  # EQ, widest with a 12-hour clock: 222 to 250


def test_classic_light_status_bar_battery_must_fit():
    ipod.status_bar(bar(), g=CLASSIC_LIGHT)  # 93 of 93
    with pytest.raises(ValueError, match=r'^Status bar battery needs 94px of 93px$'):
        ipod.status_bar(bar(), g=dataclasses.replace(CLASSIC_LIGHT, batt_w=CLASSIC_LIGHT.batt_w + 1))


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
    assert len(sites) == 71 and len(set(sites)) == 71


def test_immediate_values_set0_are_the_baked_values():
    values = ipod.immediate_values(ipod.SET0)
    assert set(values) == set(ipod.VALUE_NAMES)
    assert (values['np_bar_x'], values['np_bar_w']) == (ipod.NP_BAR[0], ipod.NP_BAR[2])
    assert ipod.VALUE_NAMES[0] == 'pitch'
    for name in ipod.VALUE_NAMES:
        if not name.startswith('np_bar'):
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
    assert dataclasses.replace(t, name='classic-light', art=52, art_pitch=52, art_body=52, art_title24_y=1, art_meta_y=29) == n
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
    assert 'img_localmusic' in named(root[3][4])  # the carousel's
    root = home(); ipod.ipod_home(root); rows = named(root[3][1])
    assert 'list_view_homelib' not in named(root) and 'img_library' not in rows and 'img_localmusic' in rows


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


def test_lists_beside_settings_rows_take_item_height(monkeypatch):
    # A library list with the set's settings row height is told apart by its item_height (Classic only).
    path = 'localmusic/albuminfo_page.bin'
    data = ipod.encode(list_page(path)); monkeypatch.setitem(ipod.UI_ASSETS, path, ipod.sha(data))
    assert named(ipod.decode(ipod.patch_asset(path, data, g=CLASSIC_LIGHT)))['list_view'][2]['item_height'] == '32'
    assert 'item_height' not in named(ipod.decode(ipod.patch_asset(path, data)))['list_view'][2]
