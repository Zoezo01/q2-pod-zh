"""language.c: the copy's lines and their English fallbacks, the card's language packs, Remove and sync."""
import ctypes as C
import struct

import pytest

from conftest import Config, Files

COPY = '/mnt/data/q2-lang.tsv'


def lines(*rows, name='Deutsch'):
    return ('# ' + name + '\n' + ''.join('\t'.join(r) + '\n' for r in rows)).encode()


@pytest.fixture
def lang(fw):
    """The copy and the card as files; stock's table as en (key -> English, the key itself when absent)."""
    class Lang:
        files = {}
        en = {}
        dirs = {}
        written = []

        def tr(self, s):
            return C.string_at(fw.fn('tr', C.c_void_p, C.c_char_p)(s.encode())).decode()

        def stock(self, key):
            return C.string_at(fw.fn('ringnav_locale_tr', C.c_void_p, C.c_void_p, C.c_char_p)(0, key.encode())).decode()

        def pack(self, app='langs', tables=None, q2app='name=Languages\nhidden=1\nlang=1\n'):
            self.files[f'/mnt/mmc/.{app}/q2app'] = q2app.encode()
            self.dirs.setdefault('/mnt/mmc', []).append(f'.{app}')
            self.dirs[f'/mnt/mmc/.{app}/lang'] = []
            for code, data in (tables or {}).items():
                self.files[f'/mnt/mmc/.{app}/lang/{code}.tsv'] = data
                self.dirs[f'/mnt/mmc/.{app}/lang'].append(f'{code}.tsv')
    s = Lang()
    s.files, s.en, s.dirs, s.written = {}, {}, {}, []
    Files(fw, s.files)
    keep, open_dirs = [], {}
    strings = {}

    def stock(info, key, *_):
        k = C.string_at(key).decode()
        if k not in s.en:
            return key
        return C.addressof(strings.setdefault(k, C.create_string_buffer(s.en[k].encode())))

    def opendir(path, *_):
        names = s.dirs.get(C.string_at(path).decode())
        if names is None:
            return 0
        handle = 0x500 + len(open_dirs)
        open_dirs[handle] = list(names)
        return handle

    def readdir(d, *_):
        if not open_dirs.get(d):
            return 0
        ent = C.create_string_buffer(268)
        name = open_dirs[d].pop(0).encode()
        C.memmove(C.addressof(ent) + 11, name, len(name))
        keep.append(ent)
        return C.addressof(ent)
    fw.mock('stock_locale_tr_trampoline', stock)
    fw.mock('fw_opendir', opendir)
    fw.mock('fw_readdir', readdir)
    fw.mock('fw_access', lambda path, *_: 0 if C.string_at(path).decode() in s.files else -1)
    fw.mock('toolsWriteConfig', lambda p, sec, key, v, *_: s.written.append(
        (C.string_at(key).decode(), C.string_at(v).decode())))
    return s


def test_english_without_a_copy(fw, lang):
    Config(fw)
    p = fw.fn('tr', C.c_void_p, C.c_void_p)
    s = C.create_string_buffer(b'Shuffle Songs')
    assert p(C.addressof(s)) == C.addressof(s)  # the same pointer: English draws as before
    lang.en['local_genre'] = 'Genres'
    assert lang.stock('local_genre') == 'Genres'


def test_lines_and_their_fallbacks(fw, lang):
    Config(fw)
    lang.en |= {'local_genre': 'Genres', 'playset_noplay': 'Require Output (Beta)', 'album': 'Album'}
    lang.files[COPY] = lines(
        ('Shuffle Songs', 'Shuffle Songs', 'Titel mischen'),
        ('local_genre', 'Genres', 'Genres DE'),
        ('playset_noplay', 'Headphones Required', 'Kopfhörer nötig'),  # written for an English since relabelled
        ('%d songs', '%d songs', '%s Titel'),  # another conversion: a crash
        ('Empty', 'Empty', ''),
        ('Two\\nlines', 'Two\\nlines', 'Zwei\\nZeilen\\tTab \\\\ \\x'),
        ('album', 'Album', 'Album DE'),
        ('Albums of %s (%d%%)', 'Albums of %s (%d%%)', 'Alben von %s (%d%%)'),
        ('short',),
    )
    assert lang.tr('Shuffle Songs') == 'Titel mischen'
    assert lang.stock('local_genre') == 'Genres DE'
    assert lang.stock('playset_noplay') == 'Require Output (Beta)'
    assert lang.tr('%d songs') == '%d songs'
    assert lang.tr('Empty') == 'Empty'
    assert lang.tr('Two\nlines') == 'Zwei\nZeilen\tTab \\ \\x'
    assert lang.stock('album') == 'Album DE'
    assert lang.tr('Albums of %s (%d%%)') == 'Alben von %s (%d%%)'
    assert lang.tr('Not in the pack') == 'Not in the pack'
    assert lang.stock('no_such_key') == 'no_such_key'  # an asset's English tr_text, untranslated
    assert lang.tr('# Deutsch') == '# Deutsch'


def test_a_duplicate_key_takes_the_line_for_stocks_english(fw, lang):
    Config(fw)
    lang.en['msg_delfile'] = 'Delete File?'
    lang.files[COPY] = lines(('msg_delfile', 'Are you sure you want to delete this file?', 'Wirklich löschen?'),
                             ('msg_delfile', 'Delete File?', 'Datei löschen?'))
    assert lang.stock('msg_delfile') == 'Datei löschen?'


@pytest.mark.parametrize('a, b, same', [
    ('%d of %d', '%d von %d', True), ('%s', '%d', False), ('%d', '', False), ('', '%d', False),
    ('%5.1f dB', '%5.1f dB', True), ('%5.1f dB', '%.1f dB', False), ('100%%', '100 %%', True),
    ('%lu', '%lu', True), ('%', '%', True), ('%s %d', '%d %s', False)])
def test_conversions(fw, a, b, same):
    f = fw.fn('language__same_conversions', C.c_int, C.c_char_p, C.c_char_p)
    assert bool(f(a.encode(), b.encode())) == same


def picks(fw):
    """How many tables the last scan found: lg's int after loaded, n and two pointers."""
    return struct.unpack_from('<i', bytes(fw.var('language__lg', C.c_char * 28)), 24)[0]


def test_the_row_needs_a_pack_with_a_table(fw, lang):
    offered = fw.fn('language_offered', C.c_int)
    Config(fw)
    assert offered() == 0
    lang.pack('other', {'de': lines()}, q2app='name=Other\n')
    lang.pack('Bad', {'de': lines()})  # not an id
    assert offered() == 0
    lang.pack('empty')
    assert offered() == 0
    lang.pack('langs', {'de': lines(), 'zh_CN': lines(name='简体中文'), 'x': b'', 'notes': None or b'# no\n'})
    lang.dirs['/mnt/mmc/.langs/lang'] += ['README.md', 'de.tsv.bak']
    assert offered() == 1 and picks(fw) == 3  # de, zh_CN and notes


def test_the_page_reads_names(fw, lang):
    Config(fw)
    lang.pack('langs', {'de': lines(), 'zh_CN': lines(name='简体中文') + b'x' * 5000, 'fr': b'# Fran\xc3\xa7ais\r\nk\te\tt\n'})
    lang.pack('more', {'de': lines(name='Deutsch (2)'), 'it': lines(name='Italiano')})
    assert fw.fn('language_offered', C.c_int)() == 1 and picks(fw) == 4  # de once, the first app's
    raw = bytes(fw.var('language__lg', C.c_char * 3000))
    for name in ('Deutsch', '简体中文', 'Français', 'Italiano'):
        assert name.encode() + b'\0' in raw
    assert b'Deutsch (2)' not in raw and b'Fran\xc3\xa7ais\r' not in raw


def test_remove_takes_the_copy(fw, lang):
    removing = fw.fn('language_removing', None, C.c_char_p)
    Config(fw, LANG='de')
    lang.pack('langs', {'de': lines()})
    lang.pack('other', {}, q2app='name=Other\n')
    lang.files[COPY] = lines(('a', 'a', 'b'))
    removing(b'.other')
    assert COPY in lang.files
    removing(b'.langs')
    assert COPY not in lang.files
    assert lang.written == []  # config.ini is the UI thread's


def test_sync_copies_a_changed_table(fw, lang):
    sync = fw.fn('language_sync', None)
    Config(fw, LANG='de')
    new = lines(('a', 'a', 'neu'))
    lang.pack('langs', {'de': new, 'fr': lines(name='Français')})
    lang.files[COPY] = lines(('a', 'a', 'alt'))
    sync()
    assert lang.files[COPY] == new and COPY + '.new' not in lang.files
    del lang.files['/mnt/mmc/.langs/lang/de.tsv']
    lang.dirs['/mnt/mmc/.langs/lang'].remove('de.tsv')
    sync()
    assert lang.files[COPY] == new  # a card without it: the copy is the Q2's


def test_sync_leaves_english(fw, lang):
    Config(fw, LANG='de')  # LANG without a copy (Remove) is English
    lang.pack('langs', {'de': lines()})
    fw.fn('language_sync', None)()
    assert COPY not in lang.files
