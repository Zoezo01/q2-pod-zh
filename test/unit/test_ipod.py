"""tools/ipod.py's page rewrites on small synthetic UI trees built to the shape each one checks for."""
import copy

import pytest

import ipod


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
    ipod.home_card(m, 2, 'coverflow', 'Coverflow')
    card = m[3][2]
    assert card[2]['name'] == 'btn_coverflow'
    image, label = card[3]
    assert image[2] == {'name': 'img_coverflow', 'style:normal:bg_image': 'menu_coverflow', 'style:focused:bg_image': 'menu_coverflowdown', 'draw': 'menu_music'}
    assert label[2] == {'name': 'label_coverflow', 'text': 'Coverflow'}
    assert m[3][1] == local_card() and len(m[3]) == 7


@pytest.mark.parametrize('image,label', [('x', 'label_localmusic'), ('img_localmusic', 'x')])
def test_home_card_wants_the_local_music_card(image, label):
    m = menu()
    m[3][1][3][0][2]['name'], m[3][1][3][1][2]['name'] = image, label
    with pytest.raises(ValueError, match='^Unexpected Local Music card$'):
        ipod.home_card(m, 2, 'coverflow', 'Coverflow')


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
    assert carousel[3][2][3][1][2]['text'] == 'Coverflow' and carousel[3][4][3][1][2]['text'] == 'Rockbox'
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
        'name': 'label_clock', 'style': 's_scrlabel_white20c', 'style:normal:font_size': '16', 'only_focus': 'true'}, []]


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
