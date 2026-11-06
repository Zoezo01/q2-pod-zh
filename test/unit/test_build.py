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
