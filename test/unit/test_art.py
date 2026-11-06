"""tools/art.py: the theme images it draws, from the shipped theme's values."""
import pathlib
import struct
import sys
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).parents[2]/'tools'))
import art
import ipod
import theme


def pixels(data):
    w, h = struct.unpack('>II', data[16:24])
    idat = data.index(b'IDAT')
    raw = zlib.decompress(data[idat + 4:idat + 4 + struct.unpack('>I', data[idat - 4:idat])[0]])
    return w, h, lambda x, y: tuple(raw[y * (4 * w + 1) + 1 + 4 * x + i] for i in range(4))


def test_classic_draws_its_images_and_the_others_none():
    themes = theme.builtins()
    assert not art.images(themes[0]) and not art.images(themes[2])
    assert set(art.images(themes[1])) == {f'classic_light_{n}' for n in ('sort_bars', 'icon_note', 'icon_folder', 'icon_stack', 'icon_game', 'icon_wave', 'icon_gear', 'chevron', 'bolt', 'plug', 'note', 'play_l', 'play_d', 'pause_l', 'pause_d')}


# The approved rendered rows, within 1 per channel: the fill down column 272 of the pause bars
# (the gradient's stops sampled at pixel centres), the edge down column 270.
REF_FILL = {4: 0x114c8f, 5: 0x1462a3, 6: 0x1870ae, 7: 0x1f83bf, 8: 0x299cd6, 9: 0x31b0e7, 10: 0x36bff2, 11: 0x38c7f6, 12: 0x35bbef,
              13: 0x2ca6e0, 14: 0x238fcd, 15: 0x1e7cba, 16: 0x196dab, 17: 0x1563a2, 18: 0x135999}
REF_EDGE = {5: 0x114d90, 6: 0x114e91, 7: 0x114f91, 8: 0x125092, 9: 0x125193, 10: 0x125293, 11: 0x125294, 12: 0x125395, 13: 0x125495,
              14: 0x125596, 15: 0x135697, 16: 0x135797, 17: 0x135898}


def test_the_play_state_stands_on_its_approved_rows():
    got = art.images(theme.builtins()[1])
    w, h, at = pixels(got['classic_light_pause_l'])
    x0, y0 = art.STATE_BOX[:2]
    assert (w, h) == (ipod.STATE_W, 22)  # the bar's box and the art's are one number
    near = lambda p, rgb: p[3] == 255 and all(abs(p[i] - (rgb >> s & 255)) <= 1 for i, s in enumerate((16, 8, 0)))
    for y, rgb in REF_FILL.items():
        assert near(at(272 - x0, y - y0), rgb), y
    for y, rgb in REF_EDGE.items():
        assert near(at(270 - x0, y - y0), rgb), y
    # its extents: the bars' rows and columns, nothing past them; no halo, as an iPod's
    ink = {(x, y) for x in range(w) for y in range(h) if at(x, y)[3]}
    assert {y for _, y in ink} == set(range(4, 19)) and {x for x, _ in ink} == {*range(270 - x0, 276 - x0), *range(277 - x0, 283 - x0)}
    assert at(276 - x0, 11 - y0)[3] == 0  # between the bars: clear
    assert got['classic_light_play_l'] == got['classic_light_play_d']
    # On Home (img_home_state at HOME_STATE_X, on the bar's rows from 0) every state's ink clears the glass by BAR_PAD.
    for name in ('play_l', 'play_d', 'pause_l', 'pause_d'):
        w, h, at = pixels(got[f'classic_light_{name}'])
        assert max(ipod.corner_inset(y) - x for x in range(w) for y in range(h) if at(x, y)[3]) == ipod.HOME_STATE_X - ipod.BAR_PAD, name


def test_the_bolt_and_the_plug_fill_the_batterys_inside():
    t = theme.builtins()[1]
    got = art.images(t)
    x0, y0 = art.BOLT_BOX[:2]
    for name in ('classic_light_bolt', 'classic_light_plug'):
        assert pixels(got[name])[:2] == tuple(art.BOLT_BOX[2:]) == (26, 13)  # the inside, x 290 to 315, rows 5 to 17
    at = pixels(got['classic_light_bolt'])[2]
    assert at(301 - x0, 10 - y0) == (0x3c, 0x4a, 0x34, 255)  # its colour, opaque
    assert t['bolt'] == 0x3c4a34 and t['bolt_halo_a'] == 102
    halo = at(305 - x0, 10 - y0)  # past the bolt's edge: white, no stronger than its alpha
    assert halo[:3] == (255, 255, 255) and 0 < halo[3] <= 102
    assert at(290 - x0, 10 - y0)[3] == 0 and at(315 - x0, 10 - y0)[3] == 0
    plug = pixels(got['classic_light_plug'])[2]
    assert plug(301 - x0, 11 - y0) == (0x3c, 0x4a, 0x34, 255) and plug(290 - x0, 11 - y0)[3] == 0


def test_the_note_is_white_and_translucent():
    w, h, at = pixels(art.images(theme.builtins()[1])['classic_light_note'])
    assert (w, h) == (100, 115)
    assert at(0, 0) == (0, 0, 0, 0)  # outside the drawing
    assert at(36, 30)[:3] == (255, 255, 255) and at(36, 30)[3] > 200  # the gloss at the stem's top
    assert at(90, 60)[:3] == (255, 255, 255) and 60 < at(90, 60)[3] < 130  # the right stem: its fill alone


def test_the_chevron_is_a_white_rounded_stroke_on_its_box():
    w, h, at = pixels(art.images(theme.builtins()[1])['classic_light_chevron'])
    assert (w, h) == (12, 16)
    assert at(8, 8) == (255, 255, 255, 255)  # the point's centreline
    assert at(0, 15)[3] == 0 and at(11, 0)[3] == 0  # outside the stroke
    assert 0 < at(1, 1)[3] < 255  # a rounded end's edge


def test_sort_is_three_white_bars():
    w, h, at = pixels(art.images(theme.builtins()[1])['classic_light_sort_bars'])
    assert (w, h) == (16, 12)
    for y in range(h):
        ink = [x for x in range(w) if at(x, y) == (255, 255, 255, 255)]
        assert ink == list(range({0: 16, 1: 16, 5: 11, 6: 11, 10: 6, 11: 6}.get(y, 0))), y
        assert all(at(x, y)[3] == 0 for x in range(w) if x not in ink), y


def test_the_pane_icons_fit_their_box_and_keep_their_holes():
    got = art.images(theme.builtins()[1])
    for name, side in (('folder', 96), ('stack', 96), ('game', 96), ('wave', 96), ('gear', 96), ('note', 100)):
        w, h, at = pixels(got[f'classic_light_icon_{name}'])
        assert w == side
        ink = [(x, y) for x in range(w) for y in range(h) if at(x, y)[3]]
        xs, ys = [p[0] for p in ink], [p[1] for p in ink]
        if name != 'note':  # 92 px on its longer side, centred on (278, 124)
            assert max(max(xs) - min(xs), max(ys) - min(ys)) in range(91, 96), name
            assert abs((min(xs) + max(xs)) / 2 + 230 - 278) <= 1 and abs((min(ys) + max(ys)) / 2 + 76 - 124) <= 1, name
    x, y = 278 - 230, 124 - 76
    assert pixels(got['classic_light_icon_gear'])[2](x, y)[3] == 0  # the gear's hole, even-odd
    assert pixels(got['classic_light_icon_folder'])[2](x, y)[3] > 0
    assert pixels(got['classic_light_icon_folder'])[2](x, y)[:3] == (255, 255, 255)


def test_path_reads_arcs_and_further_subpaths():
    polys = art.path('M0 0H10V10H0ZM20 0H30V10H20Z')
    assert len(polys) == 2 and polys[1][0] == (20, 0)
    arc = art.path('M0 0A5 5 0 1 1 10 0Z')[0]
    assert min(y for _, y in arc) >= -5.01 and max(abs(x - 5) for x, _ in arc) <= 5.01 and len(arc) > 40
    assert art.coverage([[(0, 0), (2, 0), (2, 2), (0, 2)]], (0, 0, 3, 1)) == [1.0, 1.0, 0.0]
    # a span inside another adds nothing: the union, not a sum
    assert art.coverage([[(0, 0), (3, 0), (3, 1), (0, 1)], [(1, 0), (1.5, 0), (1.5, 1), (1, 1)]], (0, 0, 3, 1)) == [1.0, 1.0, 1.0]
