"""tools/langpack.py: a table's bytes read back, and a translation checked before its table is written."""
import struct

import pytest

import langpack

TABLE = [('album', 'Album'), ('album', 'Album'), ('msg_delfile', 'Delete File?'),
         ('msg_delfile', 'Are you sure you want to delete this file?')]
ZH = [('album', 'Album', '专辑'), ('album', 'Album', '专辑'), ('msg_delfile', 'Delete File?', '是否删除文件?'),
      ('msg_delfile', 'Are you sure you want to delete this file?', '您确定要删除此文件吗？')]


def test_roundtrip():
    data = langpack.write_table(TABLE)
    assert langpack.read_table(data) == TABLE
    assert langpack.write_table(langpack.read_table(data)) == data
    assert struct.unpack_from('<HH', data, 0) == (0, len(TABLE))


def test_empty():
    assert langpack.read_table(langpack.write_table([])) == []
    assert langpack.write_table([]) == b'\0' * 12


def test_offsets():
    data = langpack.write_table(TABLE)
    start = langpack.HEADER + 8 * len(TABLE) + langpack.GAP
    offsets = struct.unpack_from(f'<{2 * len(TABLE)}I', data, langpack.HEADER)
    assert offsets[0] == start
    for (key, value), pair in zip(zip(offsets[0::2], offsets[1::2]), TABLE):
        assert data[key:].decode().startswith(pair[0])
        assert data[value:].decode().startswith(pair[1])


def test_short():
    with pytest.raises(ValueError, match='shorter than its header'):
        langpack.read_table(b'\0\0')


def test_not_zero():
    with pytest.raises(ValueError, match='starts with 3'):
        langpack.read_table(struct.pack('<HH', 3, 1))


def test_header_only():
    with pytest.raises(ValueError, match='fewer than its header'):
        langpack.read_table(struct.pack('<HH', 0, 2) + b'\0' * 8)


def test_offset_outside():
    data = bytearray(langpack.write_table(TABLE))
    struct.pack_into('<I', data, langpack.HEADER, 0)
    with pytest.raises(ValueError, match='outside the'):
        langpack.read_table(bytes(data))


def test_no_end():
    with pytest.raises(ValueError, match='has no end'):
        langpack.read_table(langpack.write_table([('album', 'Album')])[:-1])


def test_not_utf8():
    data = struct.pack('<HH2I', 0, 1, 20, 22) + b'\0' * 8 + b'\xff\0x\0'
    with pytest.raises(ValueError, match='not UTF-8'):
        langpack.read_table(data)


def test_translations():
    assert langpack.read_translations('a\tb\tc\n\nx\ty\tz\n') == [('a', 'b', 'c'), ('x', 'y', 'z')]
    with pytest.raises(ValueError, match='line 2 has 2 fields'):
        langpack.read_translations('a\tb\tc\nd\te\n')


def test_missing():
    assert langpack.check(TABLE, ZH[:3]) == ["msg_delfile: no line for 'Are you sure you want to delete this file?'"]


def test_extra():
    assert langpack.check(TABLE, [*ZH, ('other', 'x', 'y')]) == ["other: no pair in the table for 'x'"]


def test_no_text():
    assert langpack.check([('album', 'Album')], [('album', 'Album', '')]) == ['album: no text']


@pytest.mark.parametrize('source, text, ok', [
    ('Total %d songs', '共 %d 首', True), ('Total %d songs', '共首', False), ('%s by %s', '%s / %s', True),
    ('%s by %s', '%d / %s', False), ('100%% done', '100%% 完成', True)])
def test_conversions(source, text, ok):
    assert (langpack.check([('k', source)], [('k', source, text)]) == []) is ok


def test_build():
    assert langpack.read_table(langpack.build(TABLE, ZH)) == [(key, text) for key, source, text in ZH]


def test_build_duplicates():
    rows = [('album', 'Album', '专辑'), ('album', 'Album', '相册')]
    assert langpack.read_table(langpack.build([('album', 'Album')] * 2, rows)) == [('album', '专辑'), ('album', '相册')]


def test_build_refuses():
    with pytest.raises(ValueError, match='album: no text'):
        langpack.build([('album', 'Album')], [('album', 'Album', '')])


def test_main_usage(capsys):
    assert langpack.main([]) == 2
    assert langpack.main(['write']) == 2
    assert langpack.main(['build', 'a']) == 2
    assert langpack.main(['check', 'a', 'b', 'c']) == 2
    assert capsys.readouterr().err.count('usage: langpack.py build') == 4


def test_main(tmp_path, capsys):
    base, tsv, out = tmp_path/'en_US.bin', tmp_path/'zh_CN.tsv', tmp_path/'zh_CN.bin'
    base.write_bytes(langpack.write_table(TABLE))
    tsv.write_text('\n'.join('\t'.join(row) for row in ZH) + '\n', encoding='utf-8')
    assert langpack.main(['build', str(base), str(tsv), str(out)]) == 0
    assert langpack.read_table(out.read_bytes()) == [(key, text) for key, source, text in ZH]
    assert langpack.main(['check', str(base), str(tsv)]) == 0
    assert '4 pairs, 4 lines: ok' in capsys.readouterr().out
    tsv.write_text('\n'.join('\t'.join(row) for row in ZH[:3]) + '\n', encoding='utf-8')
    assert langpack.main(['check', str(base), str(tsv)]) == 1
    assert 'no line for' in capsys.readouterr().out


def test_main_refusals(tmp_path, capsys):
    tsv = tmp_path/'zh_CN.tsv'
    tsv.write_text('\t'.join(ZH[0]) + '\n', encoding='utf-8')
    assert langpack.main(['check', str(tmp_path/'none.bin'), str(tsv)]) == 2
    bad = tmp_path/'bad.bin'
    bad.write_bytes(b'nope')
    assert langpack.main(['check', str(bad), str(tsv)]) == 2
    good = tmp_path/'en_US.bin'
    good.write_bytes(langpack.write_table([('album', 'Album')]))
    broken = tmp_path/'broken.tsv'
    broken.write_text('only two\tfields\n', encoding='utf-8')
    assert langpack.main(['check', str(good), str(broken)]) == 2
    err = capsys.readouterr().err
    assert 'has 2 fields' in err and 'starts with' in err
