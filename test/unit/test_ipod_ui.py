"""tools/ipod.py's glass insets, and its Home lists on small synthetic UI trees."""
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
