"""tools/langpack.py: the texts a pack translates, read from a build and patch/, and a translation's check."""
import re
import struct

import pytest

import langpack

TABLE = [('album', 'Album'), ('album', 'Album'), ('msg_delfile', 'Delete File?'),
         ('msg_delfile', 'Are you sure you want to delete this file?'), ('genre_statistics', '%d genres')]


def table(pairs):
    """A strings/*.bin as build.py's relabel reads it: header, offsets, two zero words, then the strings."""
    offsets, strings, at = [], b'', 4 + 8 * len(pairs) + 8
    for key, value in pairs:
        key, value = key.encode(), value.encode()
        offsets += [at, at + len(key) + 1]
        strings += key + b'\0' + value + b'\0'
        at += len(key) + len(value) + 2
    return struct.pack(f'<HH{2 * len(pairs)}I', 0, len(pairs), *offsets) + b'\0' * 8 + strings


def test_read_table():
    assert langpack.read_table(table(TABLE)) == TABLE
    assert langpack.read_table(table([])) == []


@pytest.mark.parametrize('data', [b'\0', b'\1\0\0\0', struct.pack('<HH', 0, 2) + b'\0' * 15])
def test_not_a_table(data):
    with pytest.raises(ValueError, match='not a strings table'):
        langpack.read_table(data)


def test_c_literal():
    assert langpack.c_literal(['Two\\nlines\\t', ' \\"q\\" \\\\']) == 'Two\nlines\t "q" \\'
    assert langpack.c_literal(['\\xe2\\x80\\xa6', '…', '\\101\\0', '\\q']) == '……A\0\\q'


def test_escape():
    assert langpack.escape('a\\b\nc\td') == 'a\\\\b\\nc\\td'


def tree(tmp_path, files):
    (tmp_path/'patch').mkdir()
    for name, text in files.items():
        (tmp_path/'patch'/name).write_text(text, encoding='utf-8')
    return tmp_path


def test_own_strings(tmp_path):
    root = tree(tmp_path, {
        'b.c': 'toast(tr("Saved")); x = tr( "Two " "parts" ); s = str("no"); locale_tr("no");\n'
               'static const char *n[] = { N_("Songs"), N_("A \\"quote\\"") };\n'
               '/* tr("In a comment") */ widget(tr("Line\\nbreak"));',
        'a.c': 'say(tr("Saved")); say(tr("%d songs"));',
        'c.h': 'tr("Not a .c file")'})
    assert langpack.own_strings(root) == [('Saved', 'a.c'), ('%d songs', 'a.c'), ('Two parts', 'b.c'),
                                          ('Songs', 'b.c'), ('A "quote"', 'b.c'), ('Line\nbreak', 'b.c')]


def test_own_strings_of_this_tree():
    texts = dict(langpack.own_strings())
    assert texts['Restart to apply?'] == 'language.c'


def test_asset_strings():
    texts = langpack.asset_strings()
    assert {'Library', 'Playback', 'System', 'Cover Flow', 'Settings', 'Cancel', 'OK', 'Albums', 'Battery Test',
            'Kolkata', 'Status Bar', 'Show Wordmark', 'Classic Blue'} <= set(texts)
    assert 'abc' not in texts and '«»' not in texts and len(texts) == len(set(texts))


def test_template(tmp_path, monkeypatch):
    monkeypatch.setattr(langpack, 'asset_strings', lambda: ['Genre', 'Library'])
    root = tree(tmp_path, {'a.c': 'tr("Tab\\there"); tr("Genre");'})
    (tmp_path/'build/strings').mkdir(parents=True)
    (tmp_path/'build/strings/en_US.bin').write_bytes(table([('genre', 'Genre'), ('a\tb', 'x\ny')]))
    assert langpack.template(tmp_path/'build', root) == (
        'genre\tGenre\tstock\na\\tb\tx\\ny\tstock\nTab\\there\tTab\\there\ta.c\nGenre\tGenre\ta.c\n'
        'Library\tLibrary\tassets\n')


def test_read_lines():
    assert langpack.read_lines('# Deutsch\n\na\tb\tc\n  \n#x\n') == [('a', 'b', 'c')]
    with pytest.raises(ValueError, match='line 2 has 2 fields, not 3'):
        langpack.read_lines('# Deutsch\na\tb\n')


EN = [('album', 'Album', 'stock'), ('msg_delfile', 'Delete File?', 'stock'), ('%d songs', '%d songs', 'a.c'),
      ('Saved', 'Saved', 'a.c')]


def test_check_clean():
    assert langpack.check(EN, [('album', 'Album', 'Album'), ('%d songs', '%d songs', '%d Titel')]) == ([], 2)
    assert langpack.check(EN, [(k, e, e) for k, e, _ in EN]) == ([], 0)


def test_check_problems():
    problems, left = langpack.check(EN, [
        ('album', 'Albums', 'Alben'), ('msg_delfile', 'Delete File?', ''), ('%d songs', '%d songs', '%s Titel'),
        ('Saved', 'Saved', '保存 100%% ✓'), ('Gone', 'Gone', 'Weg')], font={ord(c) for c in 'Weg 1%0'})
    assert problems == [
        "album: en.tsv has no 'Albums' (a changed English, or a typo)",
        "album: the font has no 'Abln'",
        'msg_delfile: no text',
        "%d songs: '%s Titel' does not keep the conversions of '%d songs'",
        "%d songs: the font has no 'Tilst'",
        "Saved: the font has no '✓保存'",
        "Gone: en.tsv has no 'Gone' (a changed English, or a typo)"]
    assert left == 1


@pytest.mark.parametrize('en, text, same', [
    ('%d of %d', '%d von %d', True), ('%5.1f dB', '%.1f dB', False), ('100%%', '100 %%', True), ('%lu', '%lu', True),
    ('%s %d', '%d %s', False), ('%02x', '%02x', True), ('%d%%', '%d %%', True)])
def test_conversions(en, text, same):
    assert (langpack.check([('k', en, '')], [('k', en, text)])[0] == []) == same


def test_main(tmp_path, capsys):
    en, de = tmp_path/'en.tsv', tmp_path/'de.tsv'
    en.write_text('album\tAlbum\tstock\nSaved\tSaved\ta.c\n', encoding='utf-8')
    de.write_text('# Deutsch\nalbum\tAlbum\tAlbum\n', encoding='utf-8')
    assert langpack.main(['check', str(en), str(de)]) == 0
    assert capsys.readouterr().out == '1 texts left English\n'
    de.write_text('# Deutsch\nalbum\tAlbum\t\n', encoding='utf-8')
    assert langpack.main(['check', str(en), str(de)]) == 1
    assert capsys.readouterr().out == 'album: no text\n1 texts left English\n'
    assert langpack.main(['check', str(en), str(tmp_path/'none.tsv')]) == 2
    assert 'none.tsv' in capsys.readouterr().err
    assert langpack.main(['check', str(en)]) == 2
    assert langpack.main([]) == 2
    assert capsys.readouterr().err.startswith('usage: ')


def test_main_font(tmp_path, capsys):
    from fontTools.fontBuilder import FontBuilder
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(['.notdef', 'A'])
    fb.setupCharacterMap({ord('A'): 'A'})
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    fb.setupGlyf({name: TTGlyphPen(None).glyph() for name in ('.notdef', 'A')})
    fb.setupHorizontalMetrics({'.notdef': (500, 0), 'A': (500, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({'familyName': 'T', 'styleName': 'R'})
    fb.setupOS2()
    fb.setupPost()
    fb.save(tmp_path/'t.ttf')
    en, de = tmp_path/'en.tsv', tmp_path/'de.tsv'
    en.write_text('k\tK\tstock\n', encoding='utf-8')
    de.write_text('# D\nk\tK\tAB\n', encoding='utf-8')
    assert langpack.main(['check', str(en), str(de), str(tmp_path/'t.ttf')]) == 1
    assert capsys.readouterr().out == "k: the font has no 'B'\n0 texts left English\n"


def test_main_template(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(langpack, 'template', lambda build: f'from {build}\n')
    assert langpack.main(['template', 'B']) == 0
    assert capsys.readouterr().out == 'from B\n'


def marked_calls(source):
    """Each tr( or N_( call's argument text in C source, comments out."""
    source = re.sub(r'/\*.*?\*/|//[^\n]*', '', source, flags=re.S)
    for m in re.finditer(r'\b(?:tr|N_)\(', source):
        depth, i = 1, m.end()
        while depth and i < len(source):
            literal = re.compile(r'"(?:[^"\\\n]|\\.)*"').match(source, i)
            if literal:
                i = literal.end()
                continue
            depth += {'(': 1, ')': -1}.get(source[i], 0)
            i += 1
        yield source[m.end():i - 1]


def test_marked_calls():
    assert list(marked_calls('tr("a") /* tr(x ? "b" : "c") */ N_( "d" "e" ) tr(t[i]) tr(f("g)"))')) == [
        '"a"', ' "d" "e" ', 't[i]', 'f("g)")']


def test_every_literal_handed_to_tr_is_listed():
    """A literal in an expression, tr(c ? "A" : "B"), is one the template can't list: no pack translates it.
    Each literal goes alone: c ? tr("A") : tr("B")."""
    alone = re.compile(r'\s*(?:"(?:[^"\\\n]|\\.)*"\s*)+')
    for path in sorted((langpack.ROOT/'patch').glob('*.c')):
        for arg in marked_calls(path.read_text(encoding='utf-8')):
            assert '"' not in arg or alone.fullmatch(arg), f'{path.name}: tr({arg})'
