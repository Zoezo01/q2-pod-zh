"""tools/build.py's helpers: synthetic images and UI files stand in for stock's."""
import ctypes
import gc
import hashlib
import io
import pathlib
import re
import shutil
import struct
import subprocess

import pytest

import build
import ipod


def image(size=0x400000, base=0x400000, null=False):
    """A little MIPS ELF: one LOAD segment, at base from file offset 0x1000, and a final PT_NULL slot."""
    data = bytearray(0x1000 + size)
    struct.pack_into('<I', data, 28, 52)
    struct.pack_into('<HH', data, 42, 32, 2 if null else 1)
    struct.pack_into('<8I', data, 52, 1, 0x1000, base, base, size, size, 5, 0x1000)
    return data


NULL = (0,) * 8


def program(*rows, size=0x1000, phoff=52):
    """size bytes of ELF whose program headers, from phoff, are rows: (type, offset, vaddr, paddr, filesz, memsz, flags, align)."""
    data = bytearray(size)
    struct.pack_into('<I', data, 28, phoff)
    struct.pack_into('<HH', data, 42, 32, len(rows))
    for i, row in enumerate(rows):
        struct.pack_into('<8I', data, phoff + 32 * i, *row)
    return data


@pytest.mark.parametrize('number,tag', [(1, 'V001i'), (35, 'V00Zi'), (36, 'V010i'), (46655, 'VZZZi'), (46656, 'V001i'), (46657, 'V002i')])
def test_dev_tag(number, tag):
    assert build.dev_tag(number) == tag


def test_jumps():
    assert build.j(0x450000) == 0x08114000 and build.jal(0x450000) == 0x0c114000
    assert build.j(0x7) == 0x08000001 and build.jal(0x7) == 0x0c000001


class Memory:
    def __init__(self):
        self.store = {}

    def mem_write(self, address, data):
        self.store[address] = data

    def mem_read(self, address, size):
        assert size == 4
        return self.store[address]


def test_need():
    assert build.need(0, 'unused') == 0 and build.need(b'', 'unused') == b''  # only None fails
    with pytest.raises(ValueError, match=r'^Missing x$'):
        build.need(None, 'Missing x')
    with pytest.raises(ValueError, match=r'^Unexpected time zone UTC$'):
        build.timezone_offset('UTC')


def test_word_and_read():
    u = Memory()
    build.word(u, 8, -1)
    assert u.store[8] == b'\xff\xff\xff\xff' and build.read(u, 8) == 0xffffffff
    build.word(u, 12, 0x1234567890)
    assert u.store[12] == struct.pack('<I', 0x34567890) and build.read(u, 12) == 0x34567890


def test_segments():
    """Each header's file offset and its eight words, unsigned: a kernel's addresses are past 2^31."""
    rows = [(1, 0x1000, 0x80000000, 0x80000000, 0x400, 0x800, 7, 0x1000), (4, 0x1100, 0x80000100, 0x80000100, 0x24, 0x24, 4, 4), NULL]
    assert build.segments(program(*rows, phoff=64)) == [(64, rows[0]), (96, rows[1]), (128, rows[2])]


def test_segments_wants_32_byte_headers():
    data = program(NULL)
    struct.pack_into('<H', data, 42, 56)  # ELF64's
    with pytest.raises(ValueError, match='^Unexpected ELF program header size$'):
        build.segments(data)


def two_loads():
    """A note whose offset disagrees with its LOAD's, a note where nothing is loaded, then two LOAD
    segments, the first with 0x800 bytes of bss."""
    return program((4, 0x300, 0x400100, 0x400100, 0x40, 0x40, 4, 4), (4, 0x340, 0x500000, 0x500000, 0x40, 0x40, 4, 4),
                   (1, 0, 0x400000, 0x400000, 0x800, 0x1000, 5, 0x1000), (1, 0x1800, 0x411800, 0x411800, 0x100, 0x200, 6, 0x1000),
                   NULL, size=0x1900)


def test_fileoff():
    data = two_loads()
    assert [build.fileoff(data, a) for a in (0x400000, 0x400100, 0x4007ff, 0x411800, 0x4118ff)] == [0, 0x100, 0x7ff, 0x1800, 0x18ff]


@pytest.mark.parametrize('address', [0x3fffff, 0x400800, 0x410fff, 0x411900, 0x500000])
def test_fileoff_refuses_what_the_file_does_not_hold(address):
    with pytest.raises(ValueError, match=f'^Unmapped address {address:x}$'):
        build.fileoff(two_loads(), address)


class Mapper:
    def __init__(self):
        self.calls = []

    def mem_map(self, address, size):
        self.calls.append(('map', address, size))

    def mem_write(self, address, data):
        self.calls.append(('write', address, bytes(data)))


def loads():
    """A PHDR first, a LOAD ending on a page, and a LOAD from mid-page with bss past its file bytes."""
    data = program((6, 52, 0x400034, 0x400034, 0x80, 0x80, 4, 4), (1, 0, 0x400000, 0x400000, 0x200, 0x1000, 5, 0x1000),
                   (1, 0x1123, 0x411123, 0x411123, 0x10, 0x2000, 6, 0x1000), NULL, size=0x1200)
    data[0x1123:0x1133] = bytes(range(1, 17))
    return bytes(data)


def test_load_elf():
    data, u = loads(), Mapper()
    build.load_elf(u, data, 0x10000)
    assert u.calls == [('map', 0x410000, 0x1000), ('write', 0x410000, data[:0x200]),
                       ('map', 0x421000, 0x3000), ('write', 0x421123, bytes(range(1, 17)))]


def test_load_elf_at_the_link_address_by_default():
    data, u = loads(), Mapper()
    build.load_elf(u, data)
    assert [c[:2] for c in u.calls] == [('map', 0x400000), ('write', 0x400000), ('map', 0x411000), ('write', 0x411123)]


def test_machine_at_the_link_address_by_default():
    raw = image(0x2000)
    raw[0x1000] = 0x5a
    u = build.machine(bytes(raw), 0x800000)
    assert sorted(u.mem_regions())[0] == (0x400000, 0x401fff, 7) and u.mem_read(0x400000, 1) == b'\x5a'


def appendable(size=0x10001):
    """A LOAD from offset 0 (its paddr 0, as some linkers leave it), a dynamic segment inside it, and the final PT_NULL slot."""
    return program((1, 0, 0x400000, 0, 0x800, 0x1000, 5, 0x1000), (2, 0x700, 0x400700, 0x400700, 0x80, 0x80, 6, 4), NULL, size=size)


def test_append_payload():
    data = appendable()
    before = bytes(data)
    build.append_payload(data, b'\x11' * 6, 0x401001, 0x30, 'x')
    assert struct.unpack_from('<8I', data, 52 + 64) == (1, 0x20000, 0x401001, 0x401001, 6, 0x30, 7, 0x10000)
    assert data[:52 + 64] == before[:52 + 64] and data[52 + 96:0x10001] == before[52 + 96:]
    assert data[0x10001:] == bytes(0x20000 - 0x10001) + b'\x11' * 6


def test_append_payload_on_a_64k_boundary_pads_nothing():
    data = appendable(0x20000)
    build.append_payload(data, b'\x22' * 4, 0x500000, 4, 'x')
    assert len(data) == 0x20004 and data[0x20000:] == b'\x22' * 4
    assert struct.unpack_from('<2I', data, 52 + 64) == (1, 0x20000)


@pytest.mark.parametrize('base', [0x400800, 0x401000])
def test_append_payload_refuses_an_overlap(base):
    with pytest.raises(ValueError, match='^x: payload mapping overlaps$'):
        build.append_payload(appendable(), b'', base, 0, 'x')


@pytest.mark.parametrize('rows', [[(1, 0, 0x400000, 0, 0x800, 0x1000, 5, 0x1000), NULL, (2, 0x700, 0x400700, 0x400700, 0x80, 0x80, 6, 4)],
                                  [(1, 0, 0x400000, 0, 0x800, 0x1000, 5, 0x1000), NULL, NULL],
                                  [(1, 0, 0x400000, 0, 0x800, 0x1000, 5, 0x1000)]])
def test_append_payload_wants_one_final_null_slot(rows):
    with pytest.raises(ValueError, match='^x: no final PT_NULL slot$'):
        build.append_payload(program(*rows), b'', 0x500000, 0, 'x')


@pytest.mark.parametrize('label,minutes', [('GMT+0 London', 0), ('GMT-3:30 St. John\'s', -210), ('GMT+12:45 Chatham', 765),
                                           ('GMT-12 Baker Island', -720), ('GMT+5:30 Kolkata', 330)])
def test_timezone_offset(label, minutes):
    assert build.timezone_offset(label) == minutes


def zones():
    demo = image()
    start = build.fileoff(demo, 0x76b5d0)
    labels = [b'timedate_auto', *[f'stock{i}'.encode() for i in range(26)], b'timezone_gmt+12']
    demo[start:start + 28 * 64] = b''.join(l.ljust(64, b'\0') for l in labels)
    for address, old in ((0x4bce34, 0x00102980), (0x4bd090, 0x2402001c), (0x4bd4d0, 0x2402001c), (0x4fa5dc, 0x2c43001b)):
        struct.pack_into('<I', demo, build.fileoff(demo, address), old)
    return demo, start


def test_patch_timezones():
    demo, start = zones()
    build.patch_timezones(demo)
    table = demo[start:start + 28 * 64]
    rows = [table[i:i + 32].split(b'\0')[0] for i in range(0, 32 * (len(build.TIMEZONES) + 1), 32)]
    assert rows == [b'timedate_auto', *[z[0].encode() for z in build.TIMEZONES]]
    assert table[32 * (len(build.TIMEZONES) + 1):] == bytes(len(table) - 32 * (len(build.TIMEZONES) + 1))
    count = len(build.TIMEZONES) + 1
    assert [struct.unpack_from('<I', demo, build.fileoff(demo, a))[0] for a in (0x4bce34, 0x4bd090, 0x4bd4d0, 0x4fa5dc)] == [
        0x00102940, 0x24020000 | count, 0x24020000 | (count - 1), 0x2c430000 | (count - 1)]
    assert len(demo) == len(zones()[0])


@pytest.mark.parametrize('offset,value', [(0, b'x'), (27 * 64, b'x')])
def test_patch_timezones_wants_stock_labels(offset, value):
    demo, start = zones()
    demo[start + offset:start + offset + 64] = value.ljust(64, b'\0')
    with pytest.raises(ValueError, match='^Unexpected timezone labels$'):
        build.patch_timezones(demo)


def test_patch_timezones_table_must_fit(monkeypatch):
    demo, _ = zones()
    monkeypatch.setattr(build, 'TIMEZONES', build.TIMEZONES * 2)
    with pytest.raises(ValueError, match='^Timezone labels exceed stock table$'):
        build.patch_timezones(demo)


def test_patch_timezones_table_may_fill_up(monkeypatch):
    demo, start = zones()
    monkeypatch.setattr(build, 'TIMEZONES', build.TIMEZONES[:1] * 55)
    build.patch_timezones(demo)
    assert demo[start + 55 * 32:start + 55 * 32 + 8] == build.TIMEZONES[0][0].encode()[:8]
    demo, _ = zones()
    monkeypatch.setattr(build, 'TIMEZONES', build.TIMEZONES[:1] * 56)
    with pytest.raises(ValueError, match='^Timezone labels exceed stock table$'):
        build.patch_timezones(demo)


def test_patch_timezones_counts_are_fields_of_the_word(monkeypatch):
    demo, _ = zones()
    monkeypatch.setattr(build, 'TIMEZONES', build.TIMEZONES[:2])
    build.patch_timezones(demo)
    assert [struct.unpack_from('<I', demo, build.fileoff(demo, a))[0] for a in (0x4bd090, 0x4bd4d0, 0x4fa5dc)] == [0x24020003, 0x24020002, 0x2c430002]


def test_patch_timezones_label_must_fit(monkeypatch):
    demo, _ = zones()
    monkeypatch.setattr(build, 'TIMEZONES', (('x' * 32, 'UTC'),))
    with pytest.raises(ValueError, match='^Timezone labels exceed stock table$'):
        build.patch_timezones(demo)


def test_patch_timezones_longest_label_fits(monkeypatch):
    demo, start = zones()
    monkeypatch.setattr(build, 'TIMEZONES', (('x' * 31, 'UTC'),))
    build.patch_timezones(demo)
    assert demo[start + 32:start + 63] == b'x' * 31 and demo[start + 63] == 0


@pytest.mark.parametrize('address', [0x4bce34, 0x4bd090, 0x4bd4d0, 0x4fa5dc])
def test_patch_timezones_wants_stock_code(address):
    demo, _ = zones()
    struct.pack_into('<I', demo, build.fileoff(demo, address), 0)
    with pytest.raises(ValueError, match=f'^Unexpected timezone code at {address:x}$'):
        build.patch_timezones(demo)


@pytest.mark.parametrize('path', sorted(build.WEB))
def test_patch_web(path):
    text = 'head\n' + '\n'.join(old for old, _ in build.WEB[path]) + '\ntail'
    out = build.patch_web(path, text.encode()).decode()
    assert out == 'head\n' + '\n'.join(new for _, new in build.WEB[path]) + '\ntail' or all(new in out for _, new in build.WEB[path])
    assert out.startswith('head\n') and out.endswith('\ntail')


def test_patch_web_replaces_every_occurrence(monkeypatch):
    monkeypatch.setitem(build.WEB, 'x', [('a', 'b')])
    assert build.patch_web('x', b'a a') == b'b b'


def test_patch_web_wants_every_string(monkeypatch):
    monkeypatch.setitem(build.WEB, 'x', [('a', 'b'), ('expected text in this page and far more than the message keeps', 'c')])
    with pytest.raises(ValueError) as e:
        build.patch_web('x', b'a')
    assert str(e.value) == f"Unexpected x: {'expected text in this page and far more than the message keeps'[:40]!r}"


def test_patch_web_leaves_no_chinese(monkeypatch):
    monkeypatch.setitem(build.WEB, 'x', [('a', 'b')])
    for text in ('a 中'.encode(), 'a 　'.encode(), 'a ＀'.encode(), 'a ￯'.encode(), 'a 鿿'.encode()):
        with pytest.raises(ValueError, match='^Chinese left in x$'):
            build.patch_web('x', text)


def ui(*children, name='root', **props):
    return ['window', [0, 0, 375, 320], {'name': name, **props}, list(children)]


def label(text, **props):
    return ['label', [1, 2, 3, 4], {'text': text, **props}, []]


def texts(data):
    return [n[2].get('text') for n in ipod.walk(ipod.decode(data))][1:]


def test_english_ui_shared_table():
    data = ipod.encode(ui(label('文件夹'), label('plain'), label('取消'), ['view', [0, 0, 1, 1], {}, []]))
    assert texts(build.english_ui('home_page.bin', data)) == ['Folder', 'plain', 'Cancel', None]


def test_english_ui_per_asset_wins():
    data = ipod.encode(ui(label('专辑'), label('风格'), label('歌手')))
    assert texts(build.english_ui('dialog/search_dialog.bin', data)) == ['Albums', 'Genres', 'Artists']
    assert texts(build.english_ui('other.bin', data)) == ['Album', 'Genre', 'Artists']


def test_english_ui_key_test_page():
    assert texts(build.english_ui('testmode/test_key.bin', ipod.encode(ui(label('中'))))) == ['Centre']


def test_english_ui_about_page_keeps_polish():
    data = ipod.encode(ui(label('a，b'), label('plain')))
    assert texts(build.english_ui('systemset/screensaver_about.bin', data)) == ['a, b', 'plain']


def test_english_ui_about_page_refuses_real_chinese():
    with pytest.raises(ValueError, match=r'^Chinese left in systemset/screensaver_about.bin$'):
        build.english_ui('systemset/screensaver_about.bin', ipod.encode(ui(label('文件夹'))))


def test_english_ui_unknown_text_stops_the_build():
    with pytest.raises(KeyError):
        build.english_ui('home_page.bin', ipod.encode(ui(label('未知'))))


def t9():
    return ui(['view', [0, 0, 1, 1], {'name': 'upper'}, [label('中', name='page:chinese')]],
              ['view', [0, 0, 1, 1], {'name': 'lower'}, [label('中', name='page:chinese'), label('《》', name='opt:《》')]])


def test_english_ui_t9_key_toggles_case():
    root = ipod.decode(build.english_ui('kb_default_t9.bin', ipod.encode(t9())))
    upper, lower = root[3]
    assert upper[3][0][2] == {'text': 'abc', 'name': 'page:lower'}
    assert lower[3][0][2] == {'text': 'ABC', 'name': 'page:upper'}
    assert lower[3][1][2] == {'text': '«»', 'name': 'opt:«»'}


def test_english_ui_other_keyboards_keep_their_names():
    data = ipod.encode(ui(label('取消', name='page:chinese'), label('取消', name='opt:《》')))
    root = ipod.decode(build.english_ui('other.bin', data))
    assert [n[2]['name'] for n in root[3]] == ['page:chinese', 'opt:《》']


def test_english_ui_refuses_a_file_it_cannot_rewrite():
    data = bytearray(ipod.encode(ui()))
    data[4 + 31] = 1  # stray byte in the widget type's padding
    with pytest.raises(ValueError, match=r'^x: UI round trip differs$'):
        build.english_ui('x', bytes(data))


def test_bold_font():
    from fontTools.ttLib import TTFont
    font_bytes, ranges = build.bold_font()
    font = TTFont(io.BytesIO(font_bytes))
    source = TTFont(build.ROOT/'assets/fonts/LiberationSans-Bold.ttf')
    have = sorted(u for u in source.getBestCmap() if 0x20 <= u < 0x530 or 0x2000 <= u < 0x2200)
    assert sorted(font.getBestCmap()) == have and 0x41 in have and 0x2013 in have and 0x4e2d not in have
    for rec in font['name'].names:
        if rec.nameID in (1, 3, 4, 6, 16, 17, 18, 21, 22):
            assert 'Liberation' not in str(rec)
    names = {rec.nameID: str(rec) for rec in font['name'].names}
    assert names[1].startswith('Q2 Pod Sans') and names[6].startswith('Q2PodSans')
    assert names[0] == str(source['name'].getName(0, 3, 1, 0x409))  # the copyright notice stays
    assert font['head'].created == source['head'].created and font['head'].modified == source['head'].modified
    m = re.fullmatch(r'#define BOLD_RANGES \{([0-9a-fx,]+)\}', ranges)
    assert m, ranges
    bounds = [int(v, 16) for v in m[1].split(',')]
    assert sorted(u for lo, hi in zip(bounds[::2], bounds[1::2]) for u in range(lo, hi + 1)) == have
    assert bounds[:2] == [0x20, 0x7e]  # runs, not single characters
    assert [t for t in sorted(font.reader.tables, key=lambda t: font.reader.tables[t].offset)] == \
           [t for t in sorted(source.reader.tables, key=lambda t: source.reader.tables[t].offset) if t in font.reader.tables]


def test_bold_font_refuses_the_reserved_name(tmp_path, monkeypatch):
    from fontTools.ttLib import TTFont
    font = TTFont(build.ROOT/'assets/fonts/LiberationSans-Bold.ttf')
    for rec in font['name'].names:
        if rec.nameID == 4:
            rec.string = 'Liberation Mono'
    (tmp_path/'assets/fonts').mkdir(parents=True)
    font.save(tmp_path/'assets/fonts/LiberationSans-Bold.ttf')
    monkeypatch.setattr(build, 'ROOT', tmp_path)
    with pytest.raises(ValueError, match='^Bold font name 4 keeps Liberation$'):
        build.bold_font()


def test_bold_font_must_keep_its_characters(monkeypatch):
    from fontTools import subset
    keep = subset.Subsetter.populate
    monkeypatch.setattr(subset.Subsetter, 'populate', lambda self, unicodes: keep(self, unicodes=unicodes[:-1]))
    with pytest.raises(ValueError, match='^Bold font subset lost characters$'):
        build.bold_font()


def test_classic_light_font():
    from fontTools.ttLib import TTFont
    font_bytes, ranges = build.classic_light_font()
    font, source = TTFont(io.BytesIO(font_bytes)), TTFont(build.ROOT/'assets/fonts/texgyreheros-bold.otf')
    have = sorted(u for u in source.getBestCmap() if 0x20 <= u < 0x530 or 0x2000 <= u < 0x2200)
    assert sorted(font.getBestCmap()) == have and 0x41 in have and 0xe9 in have
    names = {rec.nameID: str(rec) for rec in font['name'].names}
    assert names[1].replace(' ', '').startswith('Q2PodClassicLight') and names[6].startswith('Q2PodClassicLight')  # the Windows record is TeXGyreHeros, unspaced
    assert all(len(names[i]) <= 31 for i in (1, 4, 6))  # AWTK font names
    for i in (1, 3, 4, 6, 16, 17, 18, 21, 22):
        assert 'TeX Gyre' not in names.get(i, '') and 'TeXGyre' not in names.get(i, '')
    assert names[0] == str(source['name'].getName(0, 3, 1, 0x409))
    assert names[7] == str(source['name'].getName(7, 3, 1, 0x409))
    assert 'Modified version of TeX Gyre Heros Bold' in names[10] and 'gust.org.pl' in names[10]  # LPPL 6(a), 6(b), 6(d)2
    assert (build.ROOT/'assets/fonts/README-classic-light.txt').exists() and (build.ROOT/'assets/fonts/GUST-FONT-LICENSE.txt').exists()
    if 'CFF ' in font:
        assert 'TeXGyre' not in str(font['CFF '].cff.fontNames) + font['CFF '].cff.topDictIndex[0].FullName + font['CFF '].cff.topDictIndex[0].FamilyName
    assert re.fullmatch(r'#define CLASSIC_LIGHT_RANGES \{0x20,0x7e,[0-9a-fx,]+\}', ranges)
    assert 'CFF ' in font and 'glyf' not in font  # the chosen outline form


HEART = [(50, -10), (1250, -10), (1250, 1490), (50, 1490)]
EMPTY_HEART = [(2, 84), (77, 159), (152, 84), (77, 9)]  # stock's ♡ box: x from 2, y up to 159
FONT_ORDER = ('post', 'name', 'cmap', 'OS/2', 'glyf', 'loca', 'hmtx', 'maxp', 'hhea', 'head')


def hearts_font(empty=EMPTY_HEART, composite=False, drop=()):
    """A TrueType font with the stock font's quirks: curly quotes on wide glyphs of their own, and a
    ♡ smaller than its ♥. drop leaves characters out of its Unicode cmaps. Its tables are in
    FONT_ORDER, neither sorted nor fontTools' own order."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib.sfnt import SFNTReader, SFNTWriter
    from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

    def outline(points):
        pen = TTGlyphPen(None)
        pen.moveTo(points[0])
        for p in points[1:]:
            pen.lineTo(p)
        pen.closePath()
        return pen.glyph()

    metrics = {'.notdef': (1000, 100), 'quotesingle': (600, 100), 'quotedbl': (800, 100), 'quoteleft': (2048, 100),
               'quotedblleft': (2048, 100), 'heart': (1300, 50), 'heartwhite': (2048, 2)}
    fb = FontBuilder(2048, isTTF=True)
    fb.setupGlyphOrder(list(metrics))
    cmap = {0x22: 'quotedbl', 0x27: 'quotesingle', 0x2018: 'quoteleft', 0x2019: 'quoteleft', 0x201c: 'quotedblleft',
            0x201d: 'quotedblleft', 0x2661: 'heartwhite', 0x2665: 'heart'}
    fb.setupCharacterMap({c: g for c, g in cmap.items() if c not in drop})
    glyphs = {name: outline([(100, 1000), (300, 1000), (300, 1400), (100, 1400)]) for name in metrics}
    glyphs['heart'] = outline(HEART)
    glyphs['heartwhite'] = outline(empty)
    if composite:
        pen = TTGlyphPen(glyphs)
        pen.addComponent('heart', (1, 0, 0, 1, 0, 0))
        glyphs['heartwhite'] = pen.glyph()
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=1854, descent=-434)
    fb.setupNameTable({'familyName': 'Hearts', 'styleName': 'Regular'})
    fb.setupOS2()
    fb.setupPost()
    mac = CmapSubtable.newSubtable(6)  # Mac Roman: its codes aren't Unicode (0xd4 is ‘)
    mac.platformID, mac.platEncID, mac.language = 1, 0, 0
    mac.cmap = {0x22: 'quotedbl', 0x27: 'quotesingle', 0xd4: 'quoteleft', 0xd5: 'quoteleft'}
    fb.font['cmap'].tables.append(mac)
    fb.font['head'].created = fb.font['head'].modified = 0xb0000000
    fb.font.recalcTimestamp = False
    built = io.BytesIO()
    fb.save(built)
    reader, out = SFNTReader(io.BytesIO(built.getvalue())), io.BytesIO()
    writer = SFNTWriter(out, len(reader.tables), reader.sfntVersion)
    for tag in FONT_ORDER:
        writer[tag] = reader[tag]
    writer.close()
    return out.getvalue()


def cmaps(data):
    from fontTools.ttLib import TTFont
    return {(t.platformID, t.platEncID): dict(t.cmap) for t in TTFont(io.BytesIO(data))['cmap'].tables}


def test_font_quotes_maps_curly_quotes_to_straight():
    src = hearts_font()
    before, after = cmaps(src), cmaps(build.font_quotes(src))
    unicode = [p for p in after if p != (1, 0)]
    assert unicode and sorted(after) == sorted(before)
    for p in unicode:
        assert after[p] == {**before[p], 0x2018: 'quotesingle', 0x2019: 'quotesingle', 0x201c: 'quotedbl', 0x201d: 'quotedbl'}
    assert after[(1, 0)] == before[(1, 0)]


def test_font_quotes_fits_the_empty_heart_to_the_full_one():
    from fontTools.ttLib import TTFont
    font = TTFont(io.BytesIO(build.font_quotes(hearts_font())))
    glyf, hmtx = font['glyf'], font['hmtx']
    # ♡ stretched onto ♥'s box: x 2..152 onto 50..1250 (8 times), y 9..159 onto -10..1490 (10 times)
    assert list(glyf['heartwhite'].coordinates) == [(50 + (x - 2) * 8, -10 + (y - 9) * 10) for x, y in EMPTY_HEART]
    empty = glyf['heartwhite']
    assert (empty.xMin, empty.yMin, empty.xMax, empty.yMax) == (50, -10, 1250, 1490)
    assert list(glyf['heart'].coordinates) == HEART
    assert hmtx['heart'] == hmtx['heartwhite'] == (1300 + 32, 50)  # ♥'s advance and a gap, the shared box's side bearing
    assert hmtx['quotesingle'] == (600, 100) and hmtx['quoteleft'] == (2048, 100)


def test_font_quotes_keeps_the_rest_of_the_font():
    from fontTools.ttLib import TTFont
    src = hearts_font()
    before, after = TTFont(io.BytesIO(src)), TTFont(io.BytesIO(build.font_quotes(src)))
    assert after['head'].modified == before['head'].modified == 0xb0000000  # reproducible
    assert after['head'].created == before['head'].created
    assert sorted(after.reader.tables, key=lambda t: after.reader.tables[t].offset) == list(FONT_ORDER)
    for tag in ('post', 'name', 'OS/2', 'maxp'):
        assert after.reader[tag] == before.reader[tag]


@pytest.mark.parametrize('font,message', [
    (dict(drop=(0x27,)), 'Font without straight quotes'), (dict(drop=(0x22,)), 'Font without straight quotes'),
    (dict(empty=[(x + 1, y) for x, y in EMPTY_HEART]), 'Unexpected ♡ glyph'),
    (dict(empty=[(x, y + 1) for x, y in EMPTY_HEART]), 'Unexpected ♡ glyph'),
    (dict(composite=True), 'Unexpected ♡ glyph')])
def test_font_quotes_wants_the_stock_glyphs(font, message):
    with pytest.raises(ValueError, match=f'^{message}$'):
        build.font_quotes(hearts_font(**font))


def strings_bin(pairs):
    """A strings/*.bin: u16 0, u16 pair count, a u32 file offset to each key and value, then the NUL-ended text."""
    texts = [t for pair in pairs for t in pair]
    offsets, at = [], 4 + 4 * len(texts)
    for t in texts:
        offsets.append(at)
        at += len(t) + 1
    return struct.pack(f'<HH{len(texts)}I', 0, len(pairs), *offsets) + b''.join(t + b'\0' for t in texts)


def test_relabel():
    # in an order of their own, so each change moves offsets past it that later ones have moved too;
    # each label between two others
    pairs = [(b'powerm_sleep', b'Sleep Timer')]
    for k, old, _ in reversed(build.LABELS):
        pairs += [(k, old), (k + b'_next', b'Kept')]
    new = {k: n for k, _, n in build.LABELS}
    out = build.relabel(strings_bin(pairs))
    assert isinstance(out, bytes) and out == strings_bin([(k, new.get(k, v)) for k, v in pairs])


@pytest.mark.parametrize('change', ['renamed', 'missing', 'twice'])
def test_relabel_wants_each_stock_label(change):
    pairs = [(b'powerm_sleep', b'Sleep Timer'), *[(k, old) for k, old, _ in build.LABELS]]
    key = build.LABELS[1][0]
    if change == 'renamed':
        pairs[2] = (key, build.LABELS[1][2])
    elif change == 'missing':
        del pairs[2]
    else:
        pairs.append(pairs[2])
    with pytest.raises(ValueError, match=f'^Unexpected {key.decode()} label$'):
        build.relabel(strings_bin(pairs))


SOI = b'\xff\xd8'


def segment(marker, body):
    """A JPEG marker segment: its length counts itself and the body."""
    return bytes([0xff, marker]) + struct.pack('>H', len(body) + 2) + body


def frame(marker=0xc0, bits=8, height=375, width=320, components=3):
    """A start of frame: precision, lines, samples per line, then 3 bytes for each component, numbered
    from 1 as JFIF does."""
    return segment(marker, struct.pack('>BHHB', bits, height, width, components) +
                   b''.join(bytes((c, 0x11, 0)) for c in range(1, components + 1)))


JFIF = segment(0xe0, b'JFIF\0\1\1\0\0\1\0\1\0\0')


@pytest.mark.parametrize('head', [
    JFIF + segment(0xdb, bytes(65)),
    b'\xff\xff\xff' + JFIF[1:],  # fill bytes before a marker
    b'\xff\x01\xff\xd0\xff\xd7',  # TEM and RSTn stand alone, with no length
    frame(0xc4, height=1, width=1) + frame(0xc8, height=1, width=1) + frame(0xcc, height=1, width=1),  # DHT, JPG, DAC
    segment(0xe2, bytes(0x7ffe))])  # an ICC profile's APP2 may run to 32 KB
def test_jpeg_size(head):
    assert build.jpeg_size(SOI + head + frame() + segment(0xda, bytes(10)) + b'\xff\xd9') == (320, 375)


@pytest.mark.parametrize('width,height', [(1, 1), (65535, 40000)])
def test_jpeg_size_dimensions(width, height):
    assert build.jpeg_size(SOI + JFIF + frame(width=width, height=height)) == (width, height)


NOT_BASELINE_RGB = 'Logo must be an 8-bit, three-component baseline JPEG (not grayscale/CMYK)'


@pytest.mark.parametrize('data,message', [
    (b'', 'Logo must be a JPEG'), (b'\x89PNG\r\n\x1a\n', 'Logo must be a JPEG'),
    (SOI, 'No JPEG size marker'), (SOI + JFIF, 'No JPEG size marker'),
    (SOI + b'\xff\xd9' + frame(), 'No JPEG size marker'),  # EOI: the image ended
    (SOI + b'\xff\xda' + frame(), 'No JPEG size marker'),  # a scan before any frame
    (SOI + b'\0' + frame(), 'Malformed JPEG'),
    (SOI + JFIF + b'\xff\xff', 'Truncated JPEG marker'),
    (SOI + b'\xff\x00' + frame(), 'Malformed JPEG marker'),  # a stuffed zero, not a marker
    (SOI + SOI + frame(), 'Malformed JPEG marker'),
    (SOI + b'\xff\xe0\x00', 'Truncated JPEG segment'),
    (SOI + b'\xff\xe0\x00\x00', 'Invalid JPEG segment length'), (SOI + b'\xff\xe0\x00\x01' + frame(), 'Invalid JPEG segment length'),
    (SOI + JFIF[:-1], 'Invalid JPEG segment length'), (SOI + frame()[:-1], 'Invalid JPEG segment length'),
    (SOI + segment(0xc0, struct.pack('>BHHB', 8, 375, 320, 3) + bytes(6)), 'Invalid JPEG frame length'),
    (SOI + segment(0xc0, struct.pack('>BHHB', 8, 375, 320, 2) + bytes(9)), 'Invalid JPEG frame length'),
    (SOI + segment(0xc0, struct.pack('>BHH', 8, 375, 320)), 'Invalid JPEG frame length'),
    *((SOI + frame(m), NOT_BASELINE_RGB) for m in (0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf)),
    (SOI + frame(bits=12), NOT_BASELINE_RGB), (SOI + frame(components=1), NOT_BASELINE_RGB),
    (SOI + frame(components=4), NOT_BASELINE_RGB), (SOI + frame(components=0), NOT_BASELINE_RGB),
    (SOI + frame(width=0), 'Invalid JPEG dimensions'), (SOI + frame(height=0), 'Invalid JPEG dimensions')])  # 0: DNL's
def test_jpeg_size_refuses(data, message):
    with pytest.raises(ValueError, match=f'^{re.escape(message)}$'):
        build.jpeg_size(data)


def test_source_sha256_hashes_every_input_by_path(tmp_path, monkeypatch):
    monkeypatch.setattr(build, 'ROOT', tmp_path)
    files = ['assets/icons/a.png', 'assets/b.jpg', 'patch/c.c', 'tools/d.py', 'tools/e.c', 'tools/f.h', 'tools/g.S',
             'docs/manual/build.py', 'docs/manual/guide.html']
    for name in files:
        (tmp_path/name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path/name).write_text(name)
    for name in ('tools/note.txt', 'docs/other.md', 'test/x.py', 'tools/sub/deep.py'):
        (tmp_path/name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path/name).write_text(name)
    expected = hashlib.sha256()
    for name in sorted(files):
        expected.update(name.encode() + b'\0')
        expected.update(name.encode())
    assert build.source_sha256() == expected.hexdigest()


def test_compile_helper(tmp_path, monkeypatch):
    commands = []
    def run(*args):
        commands.append(args)
        if args[0] == 'ld.lld':
            (tmp_path/'helper').write_bytes(b'linked')
    monkeypatch.setattr(build, 'run', run)
    monkeypatch.setattr(build, 'FLAGS', ['-a', '-mno-abicalls', '-b', '-G0'])
    cat = {'lib/libc.so': b'libc', 'usr/lib/libz.so': b'libz'}.__getitem__
    assert build.compile_helper(tmp_path, cat, 'tool', 'helper', ['lib/libc.so', 'usr/lib/libz.so']) == b'linked'
    assert (tmp_path/'libc.so').read_bytes() == b'libc' and (tmp_path/'libz.so').read_bytes() == b'libz'
    objs = [tmp_path/'tool.o', tmp_path/'start.o']
    flags = ('clang', '-a', '-b', '-mnan=2008', '-mabs=2008', '-mabicalls', '-c')
    assert commands[:2] == [(*flags, build.ROOT/'patch/tool.c', '-o', objs[0]), (*flags, build.ROOT/'patch/start.c', '-o', objs[1])]
    assert commands[2] == ('ld.lld', '-m', 'elf32ltsmip', '-e', '__start', '--dynamic-linker', '/lib/ld-linux-mipsn8.so.1',
                           '--image-base=0x400000', '-z', 'noexecstack', '--gc-sections', '-s', '--hash-style=sysv', '--build-id=none',
                           *objs, tmp_path/'libc.so', tmp_path/'libz.so', '-o', tmp_path/'helper')


def test_bt_silence_commands(monkeypatch):
    commands = []
    def run(*args):
        commands.append(args)
        if args[0] == 'llvm-objcopy':
            args[-1].write_bytes(b'code')
        return ''
    monkeypatch.setattr(build, 'run', run)
    monkeypatch.setattr(build, 'symbols', lambda path: {'path': path.name})
    assert build.bt_silence() == (b'code', {'path': 's.elf'})
    cc, ld, objcopy = commands
    obj, elf, binary = cc[-1], ld[-1], objcopy[-1]
    assert (obj.name, elf.name, binary.name) == ('s.o', 's.elf', 's.bin') and obj.parent == elf.parent == binary.parent
    assert cc == ('clang', '--target=mipsel-linux-gnu', '-march=mips32r2', '-mno-abicalls', '-fno-pic', '-DBUDGET=192000', '-c',
                  build.ROOT/'tools/bt_silence.S', '-o', obj)
    assert ld == ('ld.lld', '-m', 'elf32ltsmip', '-Ttext=0x450400', '-e', 'timeout', obj, '-o', elf)
    assert objcopy == ('llvm-objcopy', '-O', 'binary', '-j', '.text', elf, binary)


@pytest.mark.skipif(not shutil.which('llvm-objcopy'), reason='no LLVM')
def test_bt_silence():
    code, symbols = build.bt_silence()
    assert len(code) % 4 == 0 and len(code) > 0
    for name in ('timeout', 'fill', 'read'):
        assert build.BT_SILENCE <= symbols[name] < build.BT_SILENCE + len(code)
    assert symbols['timeout'] == build.BT_SILENCE  # its entry


def bluealsa():
    raw = image(0x4e000, 0x400000, null=True)
    raw[build.AAC_44K1] = 0xff
    for caller, _ in build.BT_PCM_CALLERS:
        struct.pack_into('<I', raw, build.fileoff(raw, caller - 8), 0x0c105174)
    for address, old, _, _ in build.BT_SILENCE_SITES:
        struct.pack_into('<I', raw, build.fileoff(raw, address), old)
    return bytes(raw)


@pytest.fixture
def silence(monkeypatch):
    symbols = {'timeout': build.BT_SILENCE, 'fill': build.BT_SILENCE + 8, 'read': build.BT_SILENCE + 16}
    monkeypatch.setattr(build, 'bt_silence', lambda: (b'\x11' * 24, symbols))
    return symbols


def payload(out):
    _, header = build.segments(out)[-1]
    assert header[:3] == (1, header[1], build.BT_DROP_PAYLOAD)
    return bytes(out[header[1]:header[1] + header[4]]), header


def test_patch_bluealsa_drops_aac_44k1(silence):
    raw = bluealsa()
    out = build.patch_bluealsa(raw)
    assert out[build.AAC_44K1] == 0
    assert isinstance(out, bytes) and raw[build.AAC_44K1] == 0xff


def test_patch_bluealsa_payload(silence):
    out = build.patch_bluealsa(bluealsa())
    data, header = payload(out)
    assert header[3] == build.BT_DROP_PAYLOAD and header[6] == 7
    assert len(data) == build.BT_SILENCE - build.BT_DROP_PAYLOAD + 24 and data[-24:] == b'\x11' * 24
    words = struct.unpack(f'<{(len(data) - 24) // 4}I', data[:-24])
    assert words[:4] == (0x27bdffe0, 0xafbf001c, 0xafb00018, 0x00808025)
    reset = 21
    assert words[reset] == 0x8fa80054
    at = reset + 1
    for caller, offset in build.BT_PCM_CALLERS:
        size = 6 if offset is not None else 9
        assert words[at:at + 4] == (0x3c0a0000 | caller >> 16, 0x354a0000 | caller & 0xffff, 0x150a0000 | size + 1, 0)
        if offset is not None:
            assert words[at + 4] == 0x264b0000 | offset
        assert words[at + 4 + size - 2:at + 4 + size] == (0x081051e4, 0)
        at += 4 + size
    assert words[at:at + 2] == (0x081051e4, 0) and set(words[at + 2:]) <= {0}


def test_patch_bluealsa_hooks(silence):
    raw = bluealsa()
    out = build.patch_bluealsa(raw)
    assert struct.unpack_from('<2I', out, build.fileoff(raw, build.BT_DROP)) == (build.j(build.BT_DROP_PAYLOAD), 0)
    reset = build.BT_DROP_PAYLOAD + 21 * 4
    assert struct.unpack_from('<2I', out, build.fileoff(raw, build.BT_DROP_HANDLER)) == (build.j(reset), 0)
    expected = {0x41465c: build.j(silence['timeout']), 0x414684: build.j(silence['fill']), 0x414688: 0x00002825,
                0x414754: build.jal(silence['read']), 0x42d09c: 0x004147a0}
    for address, new in expected.items():
        assert struct.unpack_from('<I', out, build.fileoff(raw, address))[0] == new


def test_patch_bluealsa_refuses_a_moved_caller(silence):
    raw = bytearray(bluealsa())
    struct.pack_into('<I', raw, build.fileoff(raw, 0x418114 - 8), 0)
    with pytest.raises(ValueError, match='^Bluetooth PCM caller mismatch$'):
        build.patch_bluealsa(bytes(raw))


@pytest.mark.parametrize('site', range(5))
def test_patch_bluealsa_refuses_a_moved_site(silence, site):
    raw = bytearray(bluealsa())
    struct.pack_into('<I', raw, build.fileoff(raw, build.BT_SILENCE_SITES[site][0]), 0xdead)
    with pytest.raises(ValueError, match='^Bluetooth silence site mismatch$'):
        build.patch_bluealsa(bytes(raw))


def test_patch_bluealsa_payload_may_fill_its_room(silence, monkeypatch):
    words = 21 + 1 + sum(4 + (6 if offset is not None else 9) for _, offset in build.BT_PCM_CALLERS) + 2
    monkeypatch.setattr(build, 'BT_SILENCE', build.BT_DROP_PAYLOAD + words * 4)
    assert build.patch_bluealsa(bluealsa())
    monkeypatch.setattr(build, 'BT_SILENCE', build.BT_DROP_PAYLOAD + words * 4 - 4)
    with pytest.raises(ValueError, match='^Bluetooth DROP payload too long$'):
        build.patch_bluealsa(bluealsa())


def test_patch_bluealsa_names_itself_in_a_mapping_error(silence):
    raw = bytearray(bluealsa())
    struct.pack_into('<H', raw, 44, 1)  # no final PT_NULL slot
    with pytest.raises(ValueError, match='^bluealsa: no final PT_NULL slot$'):
        build.patch_bluealsa(bytes(raw))


def test_patch_bluealsa_payload_must_fit(silence, monkeypatch):
    monkeypatch.setattr(build, 'BT_SILENCE', build.BT_DROP_PAYLOAD + 8)
    with pytest.raises(ValueError, match='^Bluetooth DROP payload too long$'):
        build.patch_bluealsa(bluealsa())


def elf(*headers, size=0x3000):
    """An ELF32 image of size bytes whose program headers, (type, offset, vaddr, filesz, memsz) each, start at 52."""
    data = bytearray(size)
    struct.pack_into('<I', data, 28, 52)
    struct.pack_into('<HH', data, 42, 32, len(headers))
    for i, (kind, offset, vaddr, filesz, memsz) in enumerate(headers):
        struct.pack_into('<8I', data, 52 + 32 * i, kind, offset, vaddr, vaddr, filesz, memsz, 5, 0x1000)
    return data


@pytest.mark.parametrize('load', [0, 0x100000])
def test_machine(load):
    from unicorn.mips_const import UC_MIPS_REG_A0
    b = elf((1, 0x1000, 0x400123, 0x10, 0x2000), (4, 0x1000, 0x800000, 0x10, 0x10), (1, 0x2000, 0x410000, 8, 0x100))
    b[0x1000:] = b'\xee' * (len(b) - 0x1000)  # bytes past a segment's filesz aren't its own
    struct.pack_into('<2I', b, 0x2000, 0x2402002a, 0xac820000)  # addiu v0, zero, 42; sw v0, 0(a0)
    u = build.machine(bytes(b), 0x2000000, load)
    # whole pages around each LOAD's memsz; nothing for the PT_NOTE
    assert sorted(r[:2] for r in u.mem_regions()) == [(load + 0x400000, load + 0x402fff), (load + 0x410000, load + 0x410fff),
                                                      (0x2000000, 0x200ffff)]
    assert all(r[2] == 7 for r in u.mem_regions())
    assert u.mem_read(load + 0x400000, 0x3000) == bytes(0x123) + b'\xee' * 0x10 + bytes(0x3000 - 0x133)
    assert u.mem_read(load + 0x410000, 0x1000)[8:] == bytes(0xff8)
    u.reg_write(UC_MIPS_REG_A0, 0x2000000)
    u.emu_start(load + 0x410000, load + 0x410008)
    assert u.mem_read(0x2000000, 4) == b'\x2a\0\0\0'  # little-endian, into the scratch


# The TagLib cover library's layout (test/m4b.py): the M4A test's beq at SITE to the MP4 extractor
# (0x1040004a, to SITE + 4 + 0x4a * 4), the next format's test after its delay slot, gp and strcasecmp's GOT slot.
SITE, MP4, NEXT, GP, STRCASECMP = 0x125ac, 0x126d8, 0x125b4, 0x327e0, 0x327e0 - 0x7e90


def taglib():
    lib = elf((1, 0x1000, 0x12000, 0x1000, 0x1000), (1, 0x2000, 0x2a000, 0x1000, 0x1000), (0, 0, 0, 0, 0))
    struct.pack_into('<I', lib, build.fileoff(lib, SITE), 0x1040004a)
    return bytes(lib)


@pytest.fixture
def tag_c(monkeypatch):
    lib = taglib()
    monkeypatch.setattr(build, 'TAG_C_SHA', hashlib.sha256(lib).hexdigest())  # the pin is stock's library
    return lib


def dispatch(lib, ext, load):
    """Where lib's cover dispatch goes for ext, entered at SITE with strcasecmp's answer for m4a in v0,
    loaded at load; and what else strcasecmp was asked to compare ext with."""
    from unicorn import UC_HOOK_CODE
    from unicorn.mips_const import (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_GP, UC_MIPS_REG_PC, UC_MIPS_REG_RA,
                                    UC_MIPS_REG_S0, UC_MIPS_REG_SP, UC_MIPS_REG_V0)
    scratch = 0x2000000
    gc.collect()  # a hooked Unicorn is in a cycle, and holds 1 GB of address space until it's collected
    u = build.machine(lib, scratch, load)
    strcasecmp, name, sp = scratch, scratch + 0x100, scratch + 0x8000
    build.word(u, load + STRCASECMP, strcasecmp)  # as the loader fills the GOT
    build.word(u, sp + 16, load + GP)  # o32: the caller's gp, saved
    u.mem_write(name, ext.encode() + b'\0')
    for reg, value in ((UC_MIPS_REG_S0, name), (UC_MIPS_REG_GP, load + GP), (UC_MIPS_REG_SP, sp),
                       (UC_MIPS_REG_V0, int(ext.lower() != 'm4a'))):
        u.reg_write(reg, value)
    compared, reached = [], []

    def string(p):
        return bytes(u.mem_read(p, 16)).split(b'\0')[0]

    def hook(u, address, size, _):
        if address == strcasecmp:
            a, b = string(u.reg_read(UC_MIPS_REG_A0)), string(u.reg_read(UC_MIPS_REG_A1))
            compared.append(b)
            u.reg_write(UC_MIPS_REG_V0, int(a.lower() != b.lower()))
            u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
        elif address - load in (MP4, NEXT):
            reached.append(address - load)
            u.emu_stop()
    u.hook_add(UC_HOOK_CODE, hook)
    u.emu_start(load + SITE, 0, count=32)
    return reached, compared


@pytest.mark.parametrize('load', [0, 0x7700000])  # a shared library goes anywhere
@pytest.mark.parametrize('ext,goes,compared', [
    ('m4a', MP4, []), ('M4A', MP4, []), ('m4b', MP4, [b'm4b']), ('M4B', MP4, [b'm4b']), ('m4B', MP4, [b'm4b']),
    ('mp3', NEXT, [b'm4b']), ('m4bx', NEXT, [b'm4b']), ('m4', NEXT, [b'm4b'])])
def test_patch_m4b_cover_sends_m4b_to_the_mp4_extractor(tag_c, load, ext, goes, compared):
    assert dispatch(build.patch_m4b_cover(tag_c), ext, load) == ([goes], compared)


def test_cover_dispatch_without_the_patch():
    assert dispatch(taglib(), 'm4b', 0) == ([NEXT], []) and dispatch(taglib(), 'm4a', 0) == ([MP4], [])


def test_patch_m4b_cover_segment(tag_c):
    out = build.patch_m4b_cover(tag_c)
    at, header = build.segments(out)[-1]
    offset = header[1]
    assert header == (1, offset, 0x30000, 0x30000, 48, 48, 5, 0x10000)  # read and execute: nothing in it changes
    assert offset % 0x10000 == 0 and offset >= len(tag_c) and len(out) == offset + 48
    assert out[len(tag_c):offset] == bytes(offset - len(tag_c)) and isinstance(out, bytes)
    words = (0x104089b5, 0, 0x8f998170, 0x02002025, 0x2785d84c, 0x0320f809, 0, 0x104089ae, 0x8fbc0010, 0x10008963, 0)
    assert out[offset:] == struct.pack('<11I', *words) + b'm4b\0'
    # in the library itself, only the M4A branch and the PT_NULL slot change
    site = build.fileoff(tag_c, SITE)
    rest = bytearray(out[:len(tag_c)])
    assert rest[site:site + 4] != tag_c[site:site + 4]
    rest[site:site + 4], rest[at:at + 32] = tag_c[site:site + 4], tag_c[at:at + 32]
    assert rest == tag_c


def test_patch_m4b_cover_wants_the_pinned_library():
    with pytest.raises(ValueError, match='^Unexpected TagLib cover library$'):
        build.patch_m4b_cover(taglib())


def test_patch_m4b_cover_wants_the_m4a_branch(monkeypatch):
    lib = bytearray(taglib())
    struct.pack_into('<I', lib, build.fileoff(lib, SITE), 0x1040004b)
    monkeypatch.setattr(build, 'TAG_C_SHA', hashlib.sha256(lib).hexdigest())
    with pytest.raises(ValueError, match='^Unexpected TagLib M4A cover branch$'):
        build.patch_m4b_cover(bytes(lib))


def test_patch_m4b_cover_names_itself_in_a_mapping_error(monkeypatch):
    raw = bytearray(taglib())
    struct.pack_into('<H', raw, 44, 2)  # no final PT_NULL slot
    monkeypatch.setattr(build, 'TAG_C_SHA', build.sha(raw))
    with pytest.raises(ValueError, match='^TagLib cover: no final PT_NULL slot$'):
        build.patch_m4b_cover(bytes(raw))


RANGES = ['#define BOLD_RANGES {0x20,0x7e}', '#define CLASSIC_LIGHT_RANGES {0x20,0x7e,0x2013,0x2014}']


def stock_h(dev=0, repo=build.REPO):
    from theme import builtins
    context = {name: f'0x{0x700000 + 16 * i:x}u' for i, name in enumerate(build.CONTEXT_DATA)}  # as demo's symbols
    return build.stock_header(context, RANGES, builtins(), dev, repo)


@pytest.mark.parametrize('dev,repo,version,tag,flag', [(0, build.REPO, f'{build.VERSION} Pod', build.VERSION, '0'),
                                                       (7, 'someone/q2-fork', f'{build.VERSION} Pod dev 7', '', '1')])
def test_stock_header_versions(dev, repo, version, tag, flag):
    header = stock_h(dev, repo)
    lines = header.splitlines()
    assert header.endswith('\n') and all(line.startswith('#define ') for line in lines)
    macros = dict(line.split(' ', 2)[1:] for line in lines)
    assert len(macros) == len(lines) and [f'#define {name} {macros[name]}' for name in ('BOLD_RANGES', 'CLASSIC_LIGHT_RANGES')] == RANGES
    assert macros['Q2POD_VERSION'] == f'"{version}"' and macros['Q2POD_DEV'] == flag
    assert macros['Q2POD_TAG'] == f'"{tag}"'  # a dev build has none, and takes any release
    assert macros['Q2POD_REPO'] == f'"{repo}"'
    assert macros['Q2POD_UA'] == f'"Q2Pod/{build.VERSION} (https://github.com/DiamondBond/q2-pod)"'  # the app's contact, not the fork's
    assert macros['bt_showcoding'] == '((const unsigned char *)0x700000u)'


@pytest.mark.skipif(not shutil.which('clang'), reason='no clang')
def test_stock_header_is_c(tmp_path):
    """stock.h as the payload's C reads it: built into a host library whose data ctypes reads back."""
    import art
    import ipod
    import theme
    themes = theme.builtins()
    (tmp_path/'stock.h').write_text(stock_h())
    (tmp_path/'h.c').write_text(f'''#include "stock.h"
const int h_ints[] = {{RING_STEP, THEME_N, THEME_KEYS, Q2POD_DEV}};
const char *const h_strings[] = {{ROCKBOX_FLAG, STOCK_VERSION, Q2POD_VERSION, Q2POD_TAG, Q2POD_REPO, Q2POD_UA}};
const char *const h_zones[] = TIMEZONE_NAMES;
const char *const h_cities[] = TIMEZONE_CITIES;
const int h_order[] = TIMEZONE_ORDER;
const char h_icons[] = SETTINGS_ICON_NAMES;
const char h_lit[] = QS_LIT;
const unsigned char *const h_context[] = {{{', '.join(build.CONTEXT_DATA)}}};
const unsigned char *const h_round[] = ROUND_COVER;
const unsigned short h_bold[] = BOLD_RANGES;
const unsigned long h_sizes[] = {{sizeof h_zones / sizeof *h_zones, sizeof h_cities / sizeof *h_cities,
    sizeof h_order / sizeof *h_order, sizeof h_icons, sizeof h_lit, sizeof h_round / sizeof *h_round, sizeof h_bold / sizeof *h_bold}};
''')
    subprocess.run(['clang', '-shared', '-fPIC', '-std=c11', '-Wall', '-Wextra', '-Werror', str(tmp_path/'h.c'), '-o', str(tmp_path/'h.so')],
                   check=True)
    lib = ctypes.CDLL(str(tmp_path/'h.so'))

    def array(kind, name, n):
        return list((kind * n).in_dll(lib, name))
    zones, cities, order, icons, lit, rounds, bold = array(ctypes.c_ulong, 'h_sizes', 7)
    assert array(ctypes.c_int, 'h_ints', 4) == [build.RING_STEP, len(themes), len(theme.theme_keys(themes)), 0]
    assert [s.decode() for s in array(ctypes.c_char_p, 'h_strings', 6)] == [
        build.ROCKBOX_FLAG, 'V1.32', f'{build.VERSION} Pod', build.VERSION, build.REPO, f'Q2Pod/{build.VERSION} (https://github.com/DiamondBond/q2-pod)']
    assert [s.decode() for s in array(ctypes.c_char_p, 'h_zones', zones)] == [zone for _, zone in build.TIMEZONES]
    # each row's city, its label after the offset
    cities = [s.decode() for s in array(ctypes.c_char_p, 'h_cities', cities)]
    assert len(cities) == len(build.TIMEZONES) and "St. John's" in cities
    assert all(re.fullmatch(rf'GMT[+-]\d+(:\d\d)? {re.escape(city)}', label) for (label, _), city in zip(build.TIMEZONES, cities))
    # every saved index once, west to east by standard offset, then by label
    order = array(ctypes.c_int, 'h_order', order)
    rows = [(build.timezone_offset(build.TIMEZONES[i][0]), build.TIMEZONES[i][0]) for i in order]
    assert sorted(order) == list(range(len(build.TIMEZONES))) and rows == sorted(rows)
    # named_in's lists: NUL-separated names, an empty one last
    icons, lit = ((ctypes.c_char * n).in_dll(lib, name).raw for n, name in ((icons, 'h_icons'), (lit, 'h_lit')))
    assert icons.endswith(b'\0\0') and all(n.endswith('.png') for n in ipod.SETTINGS_ICONS)
    assert sorted(icons[:-2].split(b'\0')) == sorted(n.removesuffix('.png').encode() for n in ipod.SETTINGS_ICONS)
    assert lit.endswith(b'\0\0') and lit[:-2].split(b'\0') == [f'drop_{n}'.encode() for n, (_, on, _) in art.QS_BUTTONS.items() if on]
    assert array(ctypes.c_void_p, 'h_context', len(build.CONTEXT_DATA)) == [0x700000 + 16 * i for i in range(len(build.CONTEXT_DATA))]
    # by radius: r rows of r pixels for each corner, NULL between
    assert rounds == max(art.CORNERS) + 1
    for r, p in enumerate(array(ctypes.c_void_p, 'h_round', rounds)):
        assert (ctypes.string_at(p, r * r) if p else None) == (bytes(art.corner_cover(r)) if r in art.CORNERS else None)
    assert array(ctypes.c_ushort, 'h_bold', bold) == [0x20, 0x7e]
