"""tools/art.py: the flat look's images (FLAT_ART), drawn for a theme whose pages.own painter is flat."""
import functools
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parents[2]/'tools'))
import art
import theme
from test_art import QS_NAMES, pixels


@functools.cache
def flat():
    """The images of a theme like Modern but with flat pages, drawn once a session."""
    return art.images({**theme.builtins()[0], 'id': 'any-theme', 'painter[PAINT_PAGES]': art.FLAT})


@pytest.fixture(autouse=True)
def afresh():
    """As test_art's: mutmut's stats pass draws afresh in each test."""
    if os.environ.get('MUTANT_UNDER_TEST') == 'stats':
        for f in [flat, *(f for f in vars(art).values() if hasattr(f, 'cache_clear'))]:
            f.cache_clear()


def ink(name):
    """name's opaque pixels' colours and its alphas."""
    w, h, at = pixels(flat()[name])
    px = [at(x, y) for x in range(w) for y in range(h)]
    return {p[:3] for p in px if p[3]}, [p[3] for p in px]


def test_the_family_is_the_looks_not_the_themes():
    got = flat()
    assert set(got) == {'flat_' + n for n in (
        *(f'drop_{b}{k}' for b in QS_NAMES for k in ('', '_l')), 'drop_dim', 'drop_dim_l',
        *(f'drop_bright{k}' for k in ('', '_l', '_ql', '_qd')), 'msg_usb_l', 'msg_usb_d', 'msg_note_l', 'msg_note_d',
        'clock_face_l', 'clock_face_d', 'spk_quiet', 'spk_quiet_l', *(f'spk_loud{k}' for k in ('', '_l', '_ql', '_qd')),
        'pane_note', 'pane_note_l')}
    assert set(art.FLAT_BUTTONS) == set(art.QS_BUTTONS)  # the same stock names: QS_LIT says which are on
    for t in theme.builtins():  # no shipped theme draws them before its theme.xml says flat
        assert t['painter[PAINT_PAGES]'] == art.FLAT or not any(k.startswith('flat_') for k in art.images(t))
    classic = art.images(theme.builtins()[1])
    assert not any(k.startswith('flat_') for k in classic)
    assert set(art.images(theme.builtins()[2])) == set(art.flat_images())  # Modern: the flat look's alone


@pytest.mark.parametrize('name, side', [('drop_wifi', 54), ('drop_dim', 24), ('drop_bright_qd', 30), ('msg_usb_d', 84), ('clock_face_l', 110)])
def test_each_image_is_its_square(name, side):
    w, h, _ = pixels(flat()['flat_' + name])
    assert (w, h) == (side, side)


@pytest.mark.parametrize('name, rgb', [('drop_wifi', 0xffffff), ('drop_sysset_l', 0x1c1c1e), ('drop_dim_l', 0x1c1c1e),
                                       ('drop_bright_ql', 0x6e6e73), ('drop_bright_qd', 0x98989d), ('msg_note_l', 0x1c1c1e),
                                       ('msg_usb_d', 0xffffff), ('spk_loud', 0xffffff), ('spk_quiet_l', 0x1c1c1e),
                                       ('spk_loud_qd', 0x98989d), ('pane_note', 0xffffff), ('pane_note_l', 0x1c1c1e)])
def test_a_glyph_is_one_ink(name, rgb):
    colours, alphas = ink('flat_' + name)
    assert colours == {tuple(rgb.to_bytes(3, 'big'))} and max(alphas) == 255


def test_the_pane_s_note_is_on_classic_s_note_box():
    """note.svg's viewBox, as Classic's note: note_x and note_y place either alike."""
    assert pixels(flat()['flat_pane_note'])[:2] == pixels(art.images(theme.builtins()[1])['classic_light_note'])[:2] == art.NOTE_BOX[2:]


def test_a_disabled_button_is_dimmed_and_low_gain_has_faint_bars():
    assert max(ink('flat_drop_highgaindisable')[1]) == round(255 * art.FLAT_DIM)
    _, low = ink('flat_drop_lowgain')
    _, high = ink('flat_drop_highgain')
    assert round(255 * art.FLAT_FAINT) in low and sum(low) < sum(high)


def test_a_glyph_sits_in_the_middle_and_the_gear_keeps_its_hole():
    for name in ('drop_wifi', 'drop_bt', 'drop_sysset', 'msg_note_d'):
        w, h, at = pixels(flat()['flat_' + name])
        xs = [x for x in range(w) for y in range(h) if at(x, y)[3] > 128]
        ys = [y for x in range(w) for y in range(h) if at(x, y)[3] > 128]
        assert abs((min(xs) + max(xs)) / 2 - w / 2) <= 2 and abs((min(ys) + max(ys)) / 2 - h / 2) <= 2, name
    assert pixels(flat()['flat_drop_sysset'])[2](27, 27)[3] == 0


def test_set_times_face_is_a_disc_with_quiet_numerals():
    for half, (rest, quiet) in art.CLOCK_FACE.items():
        w, h, at = pixels(flat()[f'flat_clock_face_{half}'])
        assert at(55, 55) == (*rest.to_bytes(3, 'big'), 255) and at(0, 0)[3] == 0
        assert (*quiet.to_bytes(3, 'big'), 255) in {at(x, y) for x in range(w) for y in range(h)}
