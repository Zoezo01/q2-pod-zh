"""Direct checks of the widget text and paint behavior in patch/font_weight.c."""

import ctypes as C
import unicodedata

import pytest

from test_nav_playing import Ui, read_wide, wide


@pytest.mark.parametrize('vowels', ['aăâeêioôơuưy', 'AĂÂEÊIOÔƠUƯY'])
@pytest.mark.parametrize('form', ['decomposed', 'shaped', 'tone_first'])
def test_normalizes_vietnamese_vowel_shapes_and_tones(fw, vowels, form):
    normalize = fw.fn('normalize_vietnamese_label', C.c_void_p, C.c_void_p, C.c_void_p)
    ui = Ui(fw)
    label = ui.widget(ui.wm, 'scrlabel_name')
    source, expected = [], []
    for vowel in vowels:
        for tone in ('', '\u0300', '\u0301', '\u0303', '\u0309', '\u0323'):
            shape = unicodedata.normalize('NFD', vowel)
            if form == 'decomposed':
                source.append(unicodedata.normalize('NFD', vowel + tone))
            elif form == 'shaped':
                source.append(vowel + tone)
            else:
                source.append(shape[0] + tone + shape[1:])
            expected.append(unicodedata.normalize('NFC', vowel + tone))

    original = ' '.join(source)
    raw = wide(original)
    result = normalize(label, C.addressof(raw))
    assert result == C.addressof(ui.texts[label])
    assert read_wide(result) == ' '.join(expected)
    assert read_wide(C.addressof(raw)) == original


@pytest.mark.parametrize('source', [None, '', 'Đức Tuấn', 'z\u0309', 'a\u0302' + 'x' * 511])
def test_keeps_original_when_there_is_no_complete_conversion(fw, source):
    normalize = fw.fn('normalize_vietnamese_label', C.c_void_p, C.c_void_p, C.c_void_p)
    raw = None if source is None else wide(source)
    address = None if raw is None else C.addressof(raw)
    assert normalize(1, address) == address
    assert not fw.calls('widget_set_text')


def test_normalizes_when_the_result_exactly_fits(fw):
    normalize = fw.fn('normalize_vietnamese_label', C.c_void_p, C.c_void_p, C.c_void_p)
    ui = Ui(fw)
    label = ui.widget(ui.wm, 'scrlabel_name')
    raw = wide('a\u0309' + 'x' * 510)
    assert read_wide(normalize(label, C.addressof(raw))) == 'ả' + 'x' * 510


def test_mark_updates_only_when_bold_changes(fw):
    mark = fw.fn('font_weight_mark', None, C.c_void_p, C.c_int)
    ui = Ui(fw)
    label = ui.widget(ui.wm, 'scrlabel_name')
    mark(None, 1)
    mark(label, -7)
    mark(label, 2)
    mark(label, 0)
    key = b'_q2_bold_fallback'
    assert [(w, value) for w, prop, value in ui.int_log if prop == key] == [(label, 1), (label, 0)]


def test_paint_repeats_only_nonempty_marked_text(fw):
    paint = fw.fn('font_weight_paint', None, C.c_void_p, C.c_void_p)
    ui = Ui(fw)
    label = ui.widget(ui.wm, 'scrlabel_name')
    canvas = 1
    log: list[tuple[int, int] | tuple[int, int, int]] = []
    fw.mock('canvas_translate', lambda c, x, y, *_: log.append((c, C.c_int(x).value, C.c_int(y).value)))
    fw.mock('widget_vtable_on_paint_self', lambda w, c, *_: log.append((w, c)))
    paint(None, canvas)
    paint(label, None)
    assert not fw.calls('widget_get_prop_int')
    ui.set_text(label, 'ả')
    paint(label, canvas)
    assert not log
    ui.ints[label, b'_q2_bold_fallback'] = 1
    paint(label, canvas)
    assert log == [(canvas, 1, 0), (label, canvas), (canvas, -1, 0)]
    log.clear()
    ui.set_text(label, '')
    paint(label, canvas)
    ui.texts.pop(label)
    paint(label, canvas)
    assert not log
