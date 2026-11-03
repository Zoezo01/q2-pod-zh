"""tools/build.py's helpers: synthetic images and UI files stand in for stock's."""
import hashlib
import io
import pathlib
import re
import shutil
import struct

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
    """A note whose offset disagrees with its LOAD's, then two LOAD segments, the first with 0x800 bytes of bss."""
    return program((4, 0x300, 0x400100, 0x400100, 0x40, 0x40, 4, 4), (1, 0, 0x400000, 0x400000, 0x800, 0x1000, 5, 0x1000),
                   (1, 0x1800, 0x411800, 0x411800, 0x100, 0x200, 6, 0x1000), NULL, size=0x1900)


def test_fileoff():
    data = two_loads()
    assert [build.fileoff(data, a) for a in (0x400000, 0x400100, 0x4007ff, 0x411800, 0x4118ff)] == [0, 0x100, 0x7ff, 0x1800, 0x18ff]


@pytest.mark.parametrize('address', [0x3fffff, 0x400800, 0x410fff, 0x411900])
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


def test_machine():
    """raw's LOAD at +load, 64 KB of scratch, and a little-endian MIPS32 core that runs it."""
    from unicorn.mips_const import UC_MIPS_REG_V0
    raw = image(0x2000)
    struct.pack_into('<I', raw, 0x1000, 0x24021234)  # addiu $v0, $zero, 0x1234
    u = build.machine(bytes(raw), 0x800000, 0x10000)
    assert sorted(u.mem_regions()) == [(0x410000, 0x411fff, 7), (0x800000, 0x80ffff, 7)]
    assert u.mem_read(0x410000, 0x2000) == raw[0x1000:]
    u.emu_start(0x410000, 0x410004)
    assert u.reg_read(UC_MIPS_REG_V0) == 0x1234


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


EMPTY_HEART = [(2, 10), (43, 60), (82, 10), (82, 159), (2, 159)]
FONT_ORDER = ('post', 'name', 'cmap', 'OS/2', 'glyf', 'loca', 'hmtx', 'maxp', 'hhea', 'head')


def quote_font(empty=EMPTY_HEART, quotes=(0x22, 0x27), composite=False):
    """A TrueType font with quotes and both hearts, ♡ drawn as empty (or as a component of it), its tables
    in FONT_ORDER, neither sorted nor fontTools' own order."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib.sfnt import SFNTReader, SFNTWriter
    shapes = {'.notdef': [(0, 0), (500, 0), (500, 700)], 'quotedbl': [(50, 500), (150, 500), (100, 700)],
              'quotesingle': [(60, 500), (120, 500), (90, 700)], 'quoteleft': [(70, 500), (130, 500), (100, 700)],
              'heart': [(10, -5), (130, -5), (130, 293), (10, 293)], 'emptyheart': empty, 'part': empty}
    glyphs = {}
    for name, points in shapes.items():
        pen = TTGlyphPen(shapes)
        if name == 'emptyheart' and composite:
            pen.addComponent('part', (1, 0, 0, 1, 0, 0))
        else:
            pen.moveTo(points[0])
            for point in points[1:]:
                pen.lineTo(point)
            pen.closePath()
        glyphs[name] = pen.glyph()
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(list(shapes))
    cmap = {0x22: 'quotedbl', 0x27: 'quotesingle', 0x2018: 'quoteleft', 0x2665: 'heart', 0x2661: 'emptyheart'}
    fb.setupCharacterMap({c: g for c, g in cmap.items() if c in quotes or c > 0x7f})
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({name: {'heart': (140, 10), 'emptyheart': (100, 2)}.get(name, (200, 0)) for name in shapes})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({'familyName': 'Quotes', 'styleName': 'Regular'})
    fb.setupOS2()
    fb.setupPost()
    fb.font.recalcTimestamp = False
    fb.font['head'].created = fb.font['head'].modified = 3000000000
    built = io.BytesIO()
    fb.font.save(built)
    reader, out = SFNTReader(io.BytesIO(built.getvalue())), io.BytesIO()
    writer = SFNTWriter(out, len(reader.tables), reader.sfntVersion)
    for tag in FONT_ORDER:
        writer[tag] = reader[tag]
    writer.close()
    return out.getvalue()


def test_font_quotes():
    from fontTools.ttLib import TTFont
    data = quote_font()
    source, font = TTFont(io.BytesIO(data)), TTFont(io.BytesIO(build.font_quotes(data)))
    straight = {0x2018: 'quotesingle', 0x2019: 'quotesingle', 0x201c: 'quotedbl', 0x201d: 'quotedbl'}
    assert [t.cmap for t in font['cmap'].tables] == [{**t.cmap, **straight} for t in source['cmap'].tables]
    empty = font['glyf']['emptyheart']  # in ♥'s box: x * 1.5 + 7, y * 2 - 25
    assert list(empty.coordinates) == [(10, -5), (72, 95), (130, -5), (130, 293), (10, 293)]
    assert (empty.xMin, empty.yMin, empty.xMax, empty.yMax) == (10, -5, 130, 293)
    assert font['hmtx']['emptyheart'] == font['hmtx']['heart'] == (172, 10)
    assert (font['head'].created, font['head'].modified) == (source['head'].created, source['head'].modified)
    assert sorted(font.reader.tables, key=lambda t: font.reader.tables[t].offset) == list(FONT_ORDER)
    assert all(font.reader[t] == source.reader[t] for t in ('post', 'name', 'OS/2', 'maxp'))


def test_font_quotes_wants_straight_quotes():
    with pytest.raises(ValueError, match='^Font without straight quotes$'):
        build.font_quotes(quote_font(quotes=(0x27,)))


@pytest.mark.parametrize('empty,composite', [([(3, 10), (82, 10), (82, 159)], False), ([(2, 10), (82, 10), (82, 160)], False),
                                             (EMPTY_HEART, True)])
def test_font_quotes_wants_stock_empty_heart(empty, composite):
    with pytest.raises(ValueError, match='^Unexpected ♡ glyph$'):
        build.font_quotes(quote_font(empty, composite=composite))


def strings(*pairs):
    """A strings/*.bin: u16 0, u16 pair count, a u32 offset to each key and value, then each text NUL-ended."""
    texts = [t for pair in pairs for t in pair]
    offsets, at = [], 4 + 8 * len(pairs)
    for t in texts:
        offsets.append(at)
        at += len(t) + 1
    return struct.pack(f'<HH{len(texts)}I', 0, len(pairs), *offsets) + b''.join(t + b'\0' for t in texts)


def test_relabel():
    """Every label, each between two others."""
    def table(column):
        pairs = [(b'first', b'Kept')]
        for label in build.LABELS:
            pairs += [(label[0], label[column]), (label[0] + b'_next', b'Kept')]
        return strings(*pairs)
    out = build.relabel(table(1))
    assert out == table(2) and isinstance(out, bytes)


@pytest.mark.parametrize('pairs', [[(b'a', b'Old')], [(b'k', b'Changed')], [(b'k', b'Old'), (b'k', b'Old')]])
def test_relabel_wants_one_stock_label(monkeypatch, pairs):
    monkeypatch.setattr(build, 'LABELS', ((b'k', b'Old', b'New'),))
    with pytest.raises(ValueError, match='^Unexpected k label$'):
        build.relabel(strings(*pairs))


def segment(marker, body):
    return bytes((0xff, marker)) + struct.pack('>H', len(body) + 2) + body


def frame(marker=0xc0, width=320, height=375, bits=8, components=3):
    """A start of frame, its components numbered from 1 as JFIF does."""
    return segment(marker, struct.pack('>BHHB', bits, height, width, components) +
                   b''.join(bytes((c, 0x11, 0)) for c in range(1, components + 1)))


def jpeg(*parts):
    return b'\xff\xd8' + b''.join(parts)


JFIF = segment(0xe0, b'JFIF\0\1\1\0\0\x48\0\x48\0\0')
BASELINE = 'Logo must be an 8-bit, three-component baseline JPEG (not grayscale/CMYK)'


def test_jpeg_size():
    tables = segment(0xdb, bytes(65)) + segment(0xc4, bytes(30))
    assert build.jpeg_size(jpeg(JFIF, tables, frame(), segment(0xda, bytes(10)), b'\xff\xd9')) == (320, 375)


@pytest.mark.parametrize('width,height', [(1, 1), (65535, 40000)])
def test_jpeg_size_dimensions(width, height):
    assert build.jpeg_size(jpeg(JFIF, frame(width=width, height=height))) == (width, height)


def test_jpeg_size_skips_fill_and_bare_markers():
    """0xff fill before a marker; TEM and RST0-7 carry no length."""
    assert build.jpeg_size(jpeg(b'\xff\xff\x01\xff\xd0\xff\xd7', JFIF, b'\xff\xff', frame())) == (320, 375)


def test_jpeg_size_skips_other_segments():
    """DHT, JPG and DAC sit among the frame markers; an ICC profile's APP2 may run to 32 KB."""
    others = segment(0xc4, b'\1') + segment(0xc8, b'\2') + segment(0xcc, b'\3') + segment(0xe2, bytes(0x7ffe))
    assert build.jpeg_size(jpeg(others, frame())) == (320, 375)


@pytest.mark.parametrize('marker', [0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf])
def test_jpeg_size_wants_baseline(marker):
    with pytest.raises(ValueError, match=f'^{re.escape(BASELINE)}$'):
        build.jpeg_size(jpeg(JFIF, frame(marker)))


@pytest.mark.parametrize('bits,components', [(12, 3), (8, 1), (8, 4), (8, 0)])
def test_jpeg_size_wants_8_bit_rgb(bits, components):
    with pytest.raises(ValueError, match=f'^{re.escape(BASELINE)}$'):
        build.jpeg_size(jpeg(JFIF, frame(bits=bits, components=components)))


@pytest.mark.parametrize('data,message', [
    (b'\x89PNG\r\n', 'Logo must be a JPEG'), (jpeg(b'\x00'), 'Malformed JPEG'),
    (jpeg(JFIF, b'\xff\xff'), 'Truncated JPEG marker'),
    (jpeg(b'\xff\x00'), 'Malformed JPEG marker'), (jpeg(b'\xff\xd8', frame()), 'Malformed JPEG marker'),
    (jpeg(b'\xff\xe0\x00'), 'Truncated JPEG segment'),
    (jpeg(b'\xff\xe0\x00\x00'), 'Invalid JPEG segment length'), (jpeg(b'\xff\xe0\x00\x01'), 'Invalid JPEG segment length'),
    (jpeg(b'\xff\xe0\x00\x05\x00\x00'), 'Invalid JPEG segment length'),
    (jpeg(segment(0xc0, b'\x08\x01\x77')), 'Invalid JPEG frame length'),
    (jpeg(JFIF, segment(0xc0, struct.pack('>BHHB', 8, 375, 320, 2) + bytes(9))), 'Invalid JPEG frame length'),
    (jpeg(JFIF, frame(width=0)), 'Invalid JPEG dimensions'), (jpeg(JFIF, frame(height=0)), 'Invalid JPEG dimensions'),
    (jpeg(JFIF, b'\xff\xe1\x00\x02'), 'No JPEG size marker'),
    (jpeg(b'\xff\xd9\x00\x02', frame()), 'No JPEG size marker'),  # the search ends at EOI
    (jpeg(b'\xff\xda\x00\x02', frame()), 'No JPEG size marker')])  # and at SOS
def test_jpeg_size_refuses(data, message):
    with pytest.raises(ValueError, match=f'^{re.escape(message)}$'):
        build.jpeg_size(data)


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
    font_bytes, ranges = build.subset_font(build.ROOT/'assets/fonts/texgyreheros-bold.otf', 'Q2 Pod Classic Light', 'CLASSIC_LIGHT_RANGES', build.CLASSIC_LIGHT_NOTICE)
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


def taglib(branch=0x1040004a):
    """libtag_c as patch_m4b_cover reads it: the M4A cover branch at 0x125ac, in a LOAD from offset 0 below
    the payload's 0x30000, a dynamic segment and the final PT_NULL slot."""
    data = program((1, 0, 0, 0, 0x20000, 0x20000, 5, 0x10000), (2, 0x1f000, 0x1f000, 0x1f000, 0x100, 0x100, 6, 4), NULL, size=0x20000)
    struct.pack_into('<I', data, 0x125ac, branch)
    return bytes(data)


def test_patch_m4b_cover(monkeypatch):
    raw = taglib()
    monkeypatch.setattr(build, 'TAG_C_SHA', build.sha(raw))
    out = build.patch_m4b_cover(raw)
    expected = bytearray(raw)
    struct.pack_into('<I', expected, 0x125ac, 0x10007694)  # b 0x30000
    struct.pack_into('<8I', expected, 52 + 64, 1, 0x20000, 0x30000, 0x30000, 48, 48, 5, 0x10000)
    words = (0x104089b5, 0, 0x8f998170, 0x02002025, 0x2785d84c, 0x0320f809, 0, 0x104089ae, 0x8fbc0010, 0x10008963, 0)
    assert out == bytes(expected) + struct.pack('<11I', *words) + b'm4b\0' and isinstance(out, bytes)


def test_patch_m4b_cover_wants_the_pinned_library():
    with pytest.raises(ValueError, match='^Unexpected TagLib cover library$'):
        build.patch_m4b_cover(taglib())


def test_patch_m4b_cover_wants_the_m4a_branch(monkeypatch):
    raw = taglib(0x1040004b)
    monkeypatch.setattr(build, 'TAG_C_SHA', build.sha(raw))
    with pytest.raises(ValueError, match='^Unexpected TagLib M4A cover branch$'):
        build.patch_m4b_cover(raw)


def test_patch_m4b_cover_names_itself_in_a_mapping_error(monkeypatch):
    raw = bytearray(taglib())
    struct.pack_into('<H', raw, 44, 2)  # no final PT_NULL slot
    monkeypatch.setattr(build, 'TAG_C_SHA', build.sha(raw))
    with pytest.raises(ValueError, match='^TagLib cover: no final PT_NULL slot$'):
        build.patch_m4b_cover(bytes(raw))


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
