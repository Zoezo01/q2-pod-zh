"""tools/art.py: the theme images it draws, from the shipped theme's values."""
import pathlib
import struct
import sys
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).parents[2]/'tools'))
import art
import theme


def pixels(data):
    w, h = struct.unpack('>II', data[16:24])
    idat = data.index(b'IDAT')
    raw = zlib.decompress(data[idat + 4:idat + 4 + struct.unpack('>I', data[idat - 4:idat])[0]])
    return w, h, lambda x, y: tuple(raw[y * (4 * w + 1) + 1 + 4 * x + i] for i in range(4))


def test_nano_draws_its_images_and_the_others_none():
    themes = theme.builtins()
    assert not art.images(themes[0]) and not art.images(themes[1])
    assert set(art.images(themes[2])) == {f'nano_{n}' for n in ('bolt', 'note', 'play_l', 'play_d', 'pause_l', 'pause_d')}


def test_the_play_state_stands_on_its_rows():
    got = art.images(theme.builtins()[2])
    w, h, at = pixels(got['nano_pause_l'])
    x0, y0 = art.STATE_BOX[:2]
    assert (w, h) == (20, 22)
    assert at(272 - x0, 11 - y0)[:3] == (0x39, 0xc7, 0xf8)[:3] or at(272 - x0, 11 - y0)[2] > 0xe0   # the bright band
    assert at(276 - x0, 11 - y0)[3] < 128  # between the bars: only the halo
    # the halo: the light half's is stronger than the dark's, outside the ink
    assert pixels(got['nano_play_l'])[2](269 - x0, 5 - y0)[3] > pixels(got['nano_play_d'])[2](269 - x0, 5 - y0)[3] > 0


def test_the_note_is_white_and_translucent():
    w, h, at = pixels(art.images(theme.builtins()[2])['nano_note'])
    assert (w, h) == (100, 115)
    assert at(0, 0) == (0, 0, 0, 0)  # outside the drawing
    assert at(36, 30)[:3] == (255, 255, 255) and at(36, 30)[3] > 200  # the gloss at the stem's top
    assert at(90, 60)[:3] == (255, 255, 255) and 60 < at(90, 60)[3] < 130  # the right stem: its fill alone


def test_path_reads_arcs_and_further_subpaths():
    polys = art.path('M0 0H10V10H0ZM20 0H30V10H20Z')
    assert len(polys) == 2 and polys[1][0] == (20, 0)
    arc = art.path('M0 0A5 5 0 1 1 10 0Z')[0]
    assert min(y for _, y in arc) >= -5.01 and max(abs(x - 5) for x, _ in arc) <= 5.01 and len(arc) > 40
    assert art.coverage([[(0, 0), (2, 0), (2, 2), (0, 2)]], (0, 0, 3, 1)) == [1.0, 1.0, 0.0]
