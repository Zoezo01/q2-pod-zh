"""tools/ipod.py's glass insets, Home lists, artist tabs and volume dialog on small synthetic UI trees, and the
ImageMagick commands behind its icons, recorded instead of run, on synthetic images audited for the test."""
import struct

import pytest

import ipod


def w(name, *children, kind='view', at=None, **props):
    return [kind, at or [0, 0, 10, 10], {'name': name, **props}, list(children)]


def named(root):
    return {n[2].get('name'): n for n in ipod.walk(root)}


@pytest.fixture
def corner(monkeypatch):
    """A 5px glass corner, whose 3-4-5 rows come out whole, with 1px of slack."""
    monkeypatch.setattr(ipod, 'CORNER_R', 5)
    monkeypatch.setattr(ipod, 'CORNER_SLACK', 1)


def test_corner_inset(corner):
    rows = [-3, 0, 1, 2, 4, 5, 160, 315, 316, 318, 319, 320, 323]
    assert [ipod.corner_inset(y) for y in rows] == [5, 5, 2, 1, 1, 0, 0, 0, 1, 1, 2, 5, 5]


def test_corner_x_takes_the_wider_end(corner):
    assert ipod.corner_x(0, 10) == 5 + 1
    assert ipod.corner_x(310, 9) == 2 + 1


LABEL = {'style': 's_scrlabel_white20l', 'only_focus': 'true', 'ellipses': 'true'}
CLEAR = {f'style:{s}:{p}': '#00000000' for s in ('normal', 'disable', 'focused') for p in ('bg_color', 'border_color')}


def home_row(i, row, label):
    """Row i: its label after the selection dot, under a full-row tap image."""
    h, x = ipod.HOME_ROW, ipod.HOME_TEXT_X
    return ['view', [0, i * h, 375, h], {'name': 'btn_' + row}, [
        ['hscroll_label', [x, 0, 375 - x - ipod.HOME_LABEL_END, h], {**LABEL, **label}, []],
        ['image', [0, 0, 375, h], {'name': 'img_' + row, 'clickable': 'true'}, []]]]


def test_home_list():
    h = ipod.HOME_ROW
    rows = [home_row(0, 'playing', {'name': 'label_playing'}),
            home_row(1, 'localmusic', {'name': 'label_library', 'text': 'Library'}),
            home_row(2, 'coverflow', {'name': 'label_coverflow', 'text': 'Cover Flow'})]
    scroll = ['scroll_view', [0, 0, 375, 3 * h],
              {'name': 'scroll_view_home', 'self_layout': 'default(x=0,y=0,w=100%,h=100%)', 'yslidable': 'true'}, rows]
    assert ipod.home_list('home', ['playing', 'localmusic', 'coverflow'], ipod.SET0, {'visible': 'false'}) == [
        'list_view', [0, ipod.HOME_TOP, 375, 3 * h], {'name': 'list_view_home', 'item_height': str(h), **CLEAR, 'visible': 'false'}, [scroll]]


def test_home_list_settings_rows():
    view = ipod.home_list('homeset', ['playset', 'sysset'], ipod.SET0)
    assert view[2] == {'name': 'list_view_homeset', 'item_height': str(ipod.HOME_ROW), **CLEAR}
    assert view[3][0][3] == [home_row(0, 'playset', {'name': 'label_playback', 'text': 'Playback'}),
                             home_row(1, 'sysset', {'name': 'label_system', 'text': 'System'})]


def artist():
    tabs = w('view_tabs', w('btn_track', kind='tab_button', text='单曲', style='s_tab'), w('btn_album', kind='tab_button', text='专辑'))
    return w('artistinfo_page', tabs, w('pages', w('view_songs'), w('view_albums'), kind='pages'))


def test_artist_tabs():
    root = artist()
    ipod.artist_tabs(root)
    n = named(root)
    assert n['btn_track'][2] == {'name': 'btn_track', 'tr_text': 'local_allsongs', 'style': 's_tab'}
    assert n['btn_album'][2] == {'name': 'btn_album', 'tr_text': 'album', 'value': 'true'}
    assert n['pages'][2] == {'name': 'pages', 'value': '1'}
    assert n['view_tabs'][2] == {'name': 'view_tabs'} and n['view_albums'][2] == {'name': 'view_albums'}


@pytest.mark.parametrize('change,message', [
    (lambda r: named(r)['btn_track'].__setitem__(0, 'button'), 'btn_track: unexpected tab'),
    (lambda r: named(r)['btn_album'][2].update(text='Albums'), 'btn_album: unexpected tab'),
    (lambda r: named(r)['pages'][2].update(value='0'), 'Unexpected artist pages'),
    (lambda r: r[3].pop(), 'Unexpected artist tabs'),
    (lambda r: r[3][0][3].pop(0), 'Unexpected artist tabs'),
])
def test_artist_tabs_wants_stock(change, message):
    root = artist()
    change(root)
    with pytest.raises(ValueError, match=f'^{message}$'):
        ipod.artist_tabs(root)


def volume():
    return w('volume_dialog', w('slider_vol', kind='slider', max='100'), w('label_vol', kind='label'),
             kind='dialog', highlight='default(alpha=200)', anim_hint='popup')


def test_volume_dialog():
    root = volume()
    ipod.volume_dialog(root)
    assert root[2] == {'name': 'volume_dialog', 'anim_hint': 'popup'}
    assert [n[2] for n in root[3]] == [{'name': 'slider_vol', 'max': '100', 'visible': 'false'}, {'name': 'label_vol', 'visible': 'false'}]


@pytest.mark.parametrize('change,message', [
    (lambda r: r.__setitem__(0, 'window'), 'Unexpected volume dialog'),
    (lambda r: r[2].update(name='x'), 'Unexpected volume dialog'),
    (lambda r: r[2].update(highlight='default(alpha=100)'), 'Unexpected volume dialog'),
    (lambda r: r[3].reverse(), 'Unexpected volume controls'),
    (lambda r: r[3][0].__setitem__(0, 'image'), 'Unexpected volume controls'),
    (lambda r: r[3][1][2].update(name='x'), 'Unexpected volume controls'),
    (lambda r: r[3].append(w('img_vol', kind='image')), 'Unexpected volume controls'),
])
def test_volume_dialog_wants_stock(change, message):
    root = volume()
    change(root)
    with pytest.raises(ValueError, match=f'^{message}$'):
        ipod.volume_dialog(root)


@pytest.fixture
def magick(monkeypatch):
    """ImageMagick's calls, recorded; each returns b'png'."""
    calls = []
    monkeypatch.setattr(ipod, 'imagemagick', lambda *args, data: calls.append((args, data)) or b'png')
    return calls


GREY = '#666666'
ARCS = ('M0.88,6.27 A8.8,8.8 0 0,1 13.12,6.27', 'M2.76,8.21 A6.1,6.1 0 0,1 11.24,8.21', 'M4.64,10.15 A3.4,3.4 0 0,1 9.36,10.15')
BT = "path 'M34.2,4 L41.2,11 L37.2,14 L37.2,2 L41.2,5 L34.2,12'"


def wifi(outer, middle, inner, dot):
    arcs = ' '.join(f"stroke '{c}' path '{p}'" for c, p in zip((outer, middle, inner), ARCS))
    return f"stroke-width 1.5 {arcs} stroke none fill '{dot}' circle 7,12.6 8.2,12.6"


@pytest.mark.parametrize('name,width,shapes', [
    ('bar_wifi0.png', 14, wifi(GREY, GREY, GREY, GREY)),
    ('bar_wifi1.png', 14, wifi(GREY, GREY, GREY, 'white')),
    ('bar_wifi2.png', 14, wifi(GREY, GREY, 'white', 'white')),
    ('bar_wifi3.png', 14, wifi(GREY, 'white', 'white', 'white')),
    ('bar_wifi4.png', 14, wifi('white', 'white', 'white', 'white')),
    ('bar_bt.png', 42, f"stroke '{GREY}' {BT}"),
    ('bar_btcon.png', 42, f"stroke 'white' {BT}"),
    ('bar_play.png', 12, "fill white stroke white stroke-width 1 path 'M2,2.5 L10,8 L2,13.5 Z'"),
    ('bar_pause.png', 12, "stroke white stroke-width 2.5 path 'M3,3 L3,13 M9,3 L9,13'"),
])
def test_status_icon(monkeypatch, magick, name, width, shapes):
    monkeypatch.setitem(ipod.STATUS_ICONS, name, ipod.sha(b'stock'))
    assert ipod.status_icon(name, b'stock') == b'png'
    draw = 'scale 4,4 fill none stroke-width 1.6 stroke-linecap round stroke-linejoin round ' + shapes
    assert magick == [(('-size', f'{4 * width}x64', 'xc:none', '-draw', draw,
                        '-filter', 'Lanczos', '-resize', f'{width}x16!', *ipod.PNG_OUT), b'')]


def test_status_icon_is_audited(monkeypatch, magick):
    monkeypatch.setitem(ipod.STATUS_ICONS, 'bar_play.png', ipod.sha(b'stock'))
    with pytest.raises(ValueError, match=r'^bar_play\.png: unaudited status icon$'):
        ipod.status_icon('bar_play.png', b'other')
    assert magick == []


@pytest.mark.parametrize('name,fill', [('play_moredown.png', '#aaaaaa'), ('play_fav.png', '#eeeeee')])
def test_quiet_icon(monkeypatch, magick, name, fill):
    monkeypatch.setitem(ipod.QUIET_ICONS, name, ipod.sha(b'stock'))
    assert ipod.quiet_icon(name, b'stock') == b'png'
    assert magick == [(('png:-', '-trim', '+repage', '-filter', 'Lanczos', '-resize', '24x24', '-channel', 'RGB', '-fill', fill,
                        '-colorize', '100', '+channel', '-gravity', 'center', '-background', 'none', '-extent', '28x28',
                        *ipod.PNG_OUT), b'stock')]


def test_quiet_icon_is_audited(monkeypatch, magick):
    monkeypatch.setitem(ipod.QUIET_ICONS, 'play_fav.png', ipod.sha(b'stock'))
    with pytest.raises(ValueError, match=r'^play_fav\.png: unaudited control icon$'):
        ipod.quiet_icon('play_fav.png', b'other')
    assert magick == []


ROW = ('png:-', '-alpha', 'on', '-filter', 'Lanczos', '-resize', f'{ipod.SET_ICON}x{ipod.SET_ICON}!', *ipod.PNG_OUT)
OPAQUE = ('png:-', '-format', '%[opaque]', 'info:')


def test_row_icon(magick):
    assert ipod.row_icon(b'icon') == b'png'
    assert magick == [(ROW, b'icon')]


def png(width, height, depth=8, colour=6):
    """A PNG signature and IHDR, all png_header reads."""
    return b'\x89PNG\r\n\x1a\n' + struct.pack('>I4sIIBBBBB', 13, b'IHDR', width, height, depth, colour, 0, 0, 0)


N = ipod.SET_STOCK_ICON
STOCK = png(N, N)
SIZED = png(ipod.SET_ICON, ipod.SET_ICON)


def resizer(monkeypatch, stock, sized, opaque):
    """Audit stock as playset_eq.png; ImageMagick resizes to sized and reports each input's opacity from opaque."""
    monkeypatch.setitem(ipod.SETTINGS_ICONS, 'playset_eq.png', ipod.sha(stock))
    calls = []

    def run(*args, data):
        calls.append((args, data))
        return opaque[data] if '-format' in args else sized
    monkeypatch.setattr(ipod, 'imagemagick', run)
    return calls


def test_settings_icon(monkeypatch):
    calls = resizer(monkeypatch, STOCK, SIZED, {STOCK: b'False', SIZED: b'False'})
    assert ipod.settings_icon('playset_eq.png', STOCK) == SIZED
    assert calls == [(ROW, STOCK), (OPAQUE, STOCK), (OPAQUE, SIZED)]


def test_settings_icon_is_audited(monkeypatch):
    calls = resizer(monkeypatch, STOCK, SIZED, {})
    with pytest.raises(ValueError, match=r'^playset_eq\.png: unaudited settings icon$'):
        ipod.settings_icon('playset_eq.png', SIZED)
    assert calls == []


@pytest.mark.parametrize('stock', [png(N - 1, N), png(N, N - 1), png(N, N, depth=16), png(N, N, colour=2)])
def test_settings_icon_wants_stock_rgba(monkeypatch, stock):
    calls = resizer(monkeypatch, stock, SIZED, {})
    with pytest.raises(ValueError, match=r'^playset_eq\.png: unexpected stock icon format$'):
        ipod.settings_icon('playset_eq.png', stock)
    assert calls == []


@pytest.mark.parametrize('sized', [png(ipod.SET_ICON + 1, ipod.SET_ICON), png(ipod.SET_ICON, ipod.SET_ICON, colour=2)])
def test_settings_icon_wants_sized_rgba(monkeypatch, sized):
    resizer(monkeypatch, STOCK, sized, {})
    with pytest.raises(ValueError, match=rf'^playset_eq\.png: filtered icon is not {ipod.SET_ICON}px RGBA$'):
        ipod.settings_icon('playset_eq.png', STOCK)


def test_settings_icon_keeps_transparency(monkeypatch):
    resizer(monkeypatch, STOCK, SIZED, {STOCK: b'False', SIZED: b'True'})
    with pytest.raises(ValueError, match=r'^playset_eq\.png: filtering changed the transparency$'):
        ipod.settings_icon('playset_eq.png', STOCK)
