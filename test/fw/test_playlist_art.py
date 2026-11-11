"""playlist_art.c's pure parts: the songs' hash that names a playlist's art, a playlist name as a FAT
file name, and the stable pick of the 2x2's four albums."""
import ctypes as C
import ctypes.util
import pathlib
import re
import sqlite3

import pytest

import host
import messy_playlists


class Songs(C.Structure):
    _fields_ = [('hash', C.c_uint), ('collect', C.c_int), ('n', C.c_uint), ('albums', C.c_void_p),
                ('songs', C.c_uint)]


ROW = C.POINTER(C.c_char_p)


def songs(fw, rows, collect=0):
    """rows (id, fileurl[, album, songer, albumsonger]) through the query's row callback, in order."""
    row = fw.fn('playlist_art__song_row', C.c_int, C.c_void_p, C.c_int, ROW, ROW)
    s = Songs(2166136261, collect, 0, None, 0)
    for r in rows:
        v = (C.c_char_p * len(r))(*(x.encode() if isinstance(x, str) else x for x in r))
        assert row(C.byref(s), len(r), v, None) == 0
    return s


def test_hash_follows_the_songs(fw):
    a, b, c = ('101', '/mnt/mmc/a.flac'), ('102', '/mnt/mmc/b.flac'), ('103', '/mnt/mmc/c.flac')
    base = songs(fw, [a, b]).hash
    assert songs(fw, [a, b]).hash == base
    assert songs(fw, [a, b, c]).hash != base and songs(fw, [a, b, c]).songs == 3  # a song added
    assert songs(fw, [a]).hash != base  # removed
    assert songs(fw, [('101', b[1]), ('102', a[1])]).hash != base  # two ids swap their songs
    assert songs(fw, []).hash == 2166136261
    # the same songs imported again (stock's Import, q2-subsonic's sync) take new ids: the same art
    assert songs(fw, [('201', a[1]), ('305', b[1])]).hash == base


def test_albums_told_apart_as_album_cmp(fw):
    rows = [('101', '/mnt/mmc/A/1.flac', 'Hits', 'X', 'Various'),
            ('102', '/mnt/mmc/B/2.flac', 'Hits', 'Y', 'Various'),  # a compilation: one album
            ('103', '/mnt/mmc/C/1.flac', '', 'Z', ''),
            ('104', '/mnt/mmc/D/1.flac', '', 'Z', ''),  # untagged: by folder
            ('105', '/mnt/mmc/D/CD2/2.flac', '', 'Z', '')]  # its second disc
    s = songs(fw, rows, collect=1)
    assert s.n == 3
    assert songs(fw, rows).n == 0  # the UI's hash keeps no albums


def test_fat_name(fw):
    escape = fw.fn('playlist_art__fat_name', None, C.c_char_p, C.c_uint, C.c_char_p)
    out = C.create_string_buffer(64)
    escape(out, 64, b'a/b:c?')
    assert out.value == b'a_b_c_'
    escape(out, 64, b'\\*"<>|\x01 Mix')
    assert out.value == b'_______ Mix'
    escape(out, 4, b'Road Trip')
    assert out.value == b'Roa'


def test_pick_is_stable_and_prefers_art(fw):
    pick = fw.fn('playlist_art__pick_order', None, C.c_uint, C.c_char_p, C.c_uint, C.POINTER(C.c_uint))
    rank = bytes([1, 0, 2, 1, 0, 0, 1, 2, 0, 1])
    def order(seed):
        out = (C.c_uint * len(rank))()
        pick(seed, rank, len(rank), out)
        return list(out)
    first = order(0x1234abcd)
    assert first == order(0x1234abcd)
    assert sorted(first) == list(range(len(rank)))
    assert [rank[i] for i in first] == sorted(rank)  # known art, then unbuilt, then known none
    assert any(order(seed) != first for seed in (1, 2, 3))


def test_quad(fw):
    quad = fw.fn('playlist_art__quad', C.c_uint, C.c_uint, C.c_uint)
    cells = {k: [quad(k, i) for i in range(4)] for k in (1, 2, 3, 4)}
    assert cells == {1: [0, 0, 0, 0], 2: [0, 1, 1, 0], 3: [0, 1, 2, 0], 4: [0, 1, 2, 3]}


class Lists(C.Structure):
    _fields_ = [('at', C.c_void_p), ('n', C.c_int)]


class ListHash(C.Structure):
    _fields_ = [('cd_num', C.c_int), ('hash', C.c_uint), ('songs', C.c_uint)]


def test_one_pass_hashes_each_playlist_as_its_own_query(fw):
    """playlist_art_refresh's rows (cd_num, id, fileurl), every playlist in cd_num then id order, give
    each playlist the hash its own query (song_row) gives, which names its art, and its count, Cover
    Flow's caption."""
    row = fw.fn('playlist_art__list_row', C.c_int, C.c_void_p, C.c_int, ROW, ROW)
    rows = [('3', '101', '/a.flac'), ('3', '104', '/b.flac'), ('5', '102', '/c.flac'), ('9', '103', '/a.flac')]
    at = (ListHash * 128)()
    t = Lists(C.addressof(at), 0)
    for r in rows:
        assert row(C.byref(t), 3, (C.c_char_p * 3)(*(x.encode() for x in r)), None) == 0
    assert t.n == 3
    for i, cd in enumerate(('3', '5', '9')):
        mine = [r[1:] for r in rows if r[0] == cd]
        assert at[i].cd_num == int(cd) and at[i].hash == songs(fw, mine).hash and at[i].songs == len(mine)
    t.n = 128  # full: a further playlist's rows are left out, not hashed into the last one's
    assert row(C.byref(t), 3, (C.c_char_p * 3)(b'11', b'105', b'/d.flac'), None) == 0
    assert t.n == 128 and at[127].cd_num == 0 and at[127].hash == 0 and at[127].songs == 0


class Pl(C.Structure):  # playlist_art.c's pl
    _fields_ = [('jobs', C.c_void_p), ('total', C.c_int), ('done', C.c_int), ('told', C.c_int), ('idle', C.c_int),
                ('running', C.c_int), ('cancel', C.c_int), ('thread', C.c_ulong), ('timer', C.c_uint),
                ('lists', Lists), ('want_lists', C.c_int), ('fresh', C.c_void_p)]


@pytest.fixture(scope='module')
def messy(tmp_path_factory):
    """test/fw/messy_playlists.py's table: its path and each playlist's rows in id order."""
    path = tmp_path_factory.mktemp('messy') / 'database.db'
    return path, messy_playlists.build(str(path))


def test_a_playlist_of_20k_albums_keeps_64_and_hashes_every_song(fw, messy):
    """PL_ALBUMS: the 2x2 picks from the first 64 albums by id, so the dedup, the heap and the pick stay
    bounded; the hash and count still cover every song, so the art's name is the same as the UI's."""
    rows = messy[1][1]
    whole = songs(fw, rows, collect=1)
    assert whole.n == 64 and whole.songs == 20000 and whole.hash == songs(fw, [r[:2] for r in rows]).hash


def test_a_cancel_stops_the_read(fw):
    """A join waiting on Return: the worker's read stops at the next row, nothing named or marked."""
    row = fw.fn('playlist_art__song_row', C.c_int, C.c_void_p, C.c_int, ROW, ROW)
    pl = fw.var('playlist_art__pl', Pl)
    s = Songs(2166136261, 1, 0, None, 0)
    v = (C.c_char_p * 5)(b'101', b'/a.flac', b'A', b'X', b'')
    pl.cancel = 1
    assert row(C.byref(s), 5, v, None) == 1 and s.songs == 0
    pl.cancel = 0
    assert row(C.byref(s), 5, v, None) == 0 and s.songs == 1 and s.n == 1


SOURCE = (pathlib.Path(__file__).parents[2] / 'patch/playlist_art.c').read_text()
PL_HASHES = host.tools_build.need(re.search(r'#define PL_HASHES (".*")', SOURCE), 'No PL_HASHES')[1][1:-1]
PL_PAGE = int(host.tools_build.need(re.search(r'#define PL_PAGE (\d+)', SOURCE), 'No PL_PAGE')[1])


def test_the_refresh_reads_a_page_of_ids_in_rowid_order_with_no_sort():
    """PL_HASHES on stock's schema (no index on cd_num): a rowid range, no TEMP B-TREE, so no sort and no
    spill of a big table to /tmp (tmpfs, the Q2's RAM), and a page reads PL_PAGE ids, not the table."""
    db = sqlite3.connect(':memory:')
    db.execute(messy_playlists.DDL)
    plan = ' '.join(str(r[-1]) for r in db.execute('explain query plan ' + PL_HASHES % (100, 100 + PL_PAGE)))
    assert 'TEMP B-TREE' not in plan and 'INTEGER PRIMARY KEY (rowid>? AND rowid<?)' in plan and 'order by id' in PL_HASHES


def all_rows(by_cd):
    """Every song row as PL_HASHES gives it: (cd_num, id, fileurl) in id order."""
    rows = [(cd, r[0], r[1]) for cd, songs in by_cd.items() for r in songs]
    return sorted(rows, key=lambda r: int(r[1]))


def text(x):
    return x.encode() if isinstance(x, str) else x


def test_one_pass_in_id_order_hashes_each_messy_playlist_as_its_own_query(fw, messy):
    """The refresh's rows interleave the playlists: each still gets its own query's hash and count, and
    junk cd_nums (NULL, -1, 999), no playlist's, take no slot."""
    row = fw.fn('playlist_art__list_row', C.c_int, C.c_void_p, C.c_int, ROW, ROW)
    by_cd = messy[1]
    at = (ListHash * 128)()
    t = Lists(C.addressof(at), 0)
    for cd, id_, url in all_rows(by_cd):
        v = (C.c_char_p * 3)(None if cd is None else str(cd).encode(), id_.encode(), text(url))
        assert row(C.byref(t), 3, v, None) == 0
    assert t.n == 101  # the 101 playlists alone, cd 0 among them
    got = {at[i].cd_num: (at[i].hash, at[i].songs) for i in range(t.n)}
    for cd, songs_ in by_cd.items():
        if cd is None or not 0 <= cd <= 100:
            assert cd not in got
            continue
        mine = songs(fw, [r[:2] for r in songs_])
        assert got[cd] == (mine.hash, len(songs_)), cd
    t.n = 128  # full: those it has go on counting
    v = (C.c_char_p * 3)(b'4', b'99999', b'/x.flac')
    before = got[4][1]
    assert row(C.byref(t), 3, v, None) == 0 and next(at[i].songs for i in range(101) if at[i].cd_num == 4) == before + 1
    assert row(C.byref(t), 3, (C.c_char_p * 3)(b'777', b'99998', b'/y.flac'), None) == 0  # no playlist's
    assert all(at[i].cd_num != 777 for i in range(128))


def real_sqlite(fw, path, each=None):
    """playlist_art.c's SQLite calls on the real library at path; each(sql) runs before each statement."""
    lib = C.CDLL(ctypes.util.find_library('sqlite3'))
    lib.sqlite3_open.argtypes = [C.c_char_p, C.c_void_p]
    lib.sqlite3_exec.argtypes = [C.c_void_p] * 5
    lib.sqlite3_close.argtypes = [C.c_void_p]
    lib.sqlite3_status64.argtypes = [C.c_int, C.POINTER(C.c_int64), C.POINTER(C.c_int64), C.c_int]
    sqls = []
    def exec_(db, sql, cb, ctx, err, *_):
        sqls.append(C.string_at(sql))
        if each:
            each(sqls[-1])
        return lib.sqlite3_exec(db, sql, cb, ctx, err)
    fw.mock('sqlite3_open', lambda name, db, *_: lib.sqlite3_open(str(path).encode(), db))
    fw.mock('sqlite3_exec', exec_)
    fw.mock('sqlite3_close', lambda db, *_: lib.sqlite3_close(db))
    return lib, sqls


def refresh(fw):
    """The worker's refresh (query with PL_HASHES through list_row): its result and table."""
    at = (ListHash * 128)()
    t = Lists(C.addressof(at), 0)
    query = fw.fn('playlist_art__query', C.c_int, C.c_char_p, C.c_int, C.c_void_p, C.c_void_p)
    ok = query(PL_HASHES.encode(), 0, C.cast(fw.lib.playlist_art__list_row, C.c_void_p), C.byref(t))
    return ok, t, at


def test_the_refresh_s_sqlite_heap_stays_small(fw, messy):
    """query() sets a 64-page cache before its statements: the whole messy table's refresh, through real
    SQLite, peaks under 1 MB of SQLite heap (no pragma: the default 2 MB cache fills)."""
    path, by_cd = messy
    lib, sqls = real_sqlite(fw, path)
    cur, high = C.c_int64(), C.c_int64()
    lib.sqlite3_status64(0, C.byref(cur), C.byref(high), 1)  # SQLITE_STATUS_MEMORY_USED, its highwater reset
    ok, t, _ = refresh(fw)
    lib.sqlite3_status64(0, C.byref(cur), C.byref(high), 0)
    assert ok == 1 and sqls[0].lower() == b'pragma cache_size=64' and t.n == 101
    assert high.value - cur.value < 1 << 20, high.value
    print(f'refresh of {sum(map(len, by_cd.values()))} rows: SQLite heap peak {(high.value - cur.value) // 1024} KB')


def test_a_writer_commits_between_two_pages_of_a_read(fw, messy, tmp_path):
    """The read holds SQLite's shared lock for one page of PL_PAGE ids, a statement each, and none between:
    a writer that won't wait at all (stock's Import, say) commits before every page, and the read still
    hashes every playlist as a read of the table alone does."""
    path = tmp_path / 'database.db'
    path.write_bytes(messy[0].read_bytes())
    writer = sqlite3.connect(path, timeout=0, isolation_level=None)
    writer.execute('create table w(x)')
    pages = []
    def write(sql):
        if sql.startswith(b'select cd_num'):
            m = re.search(rb'id>(\d+) and id<=(\d+)', sql)
            assert m, sql
            lo, hi = map(int, m.groups())
            assert hi - lo == PL_PAGE
            pages.append(lo)
            writer.execute('insert into w values (?)', (lo,))  # sqlite3.OperationalError: locked, if the read held on
    real_sqlite(fw, path, write)
    ok, t, at = refresh(fw)
    assert ok == 1 and len(pages) > 50 and writer.execute('select count(*) from w').fetchone()[0] == len(pages)
    real_sqlite(fw, messy[0])
    alone, u, at2 = refresh(fw)
    assert alone == 1 and t.n == u.n == 101
    assert [(at[i].cd_num, at[i].hash, at[i].songs) for i in range(101)] == [(at2[i].cd_num, at2[i].hash, at2[i].songs) for i in range(101)]


def test_usb_storage_stops_the_art_worker_before_stock_hands_over_the_card(fw):
    """A first build may hold card files (ffmpeg, an fopen) and write .coverflow while the PC owns the
    card: USB Storage joins the worker before stock's page."""
    pl = fw.var('playlist_art__pl', Pl)
    pl.running, pl.thread = 1, 0x77
    order: list = []
    fw.mock('fw_pthread_join', lambda t, *_: order.append(('join', t, pl.cancel)) and 0)
    fw.mock('stock_usbmode_trampoline', lambda *_: order.append(('stock', pl.running)) or 3)
    assert fw.fn('ringnav_usbmode', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p)(None, None, None, None) == 3
    assert order == [('join', 0x77, 1), ('stock', 0)] and pl.cancel == 0


class Mallinfo2(C.Structure):
    _fields_ = [(f, C.c_size_t) for f in ('arena', 'ordblks', 'smblks', 'hblks', 'hblkhd', 'usmblks', 'fsmblks',
                                           'uordblks', 'fordblks', 'keepcost')]


def test_a_playlist_of_huge_tags_keeps_a_small_heap(fw, messy):
    """cd 2: 500 albums with 4 KB tags and 800-byte non-UTF-8 fileurls; the worker keeps 64 albums' fileurls
    (about 52 KB), not 500 (400 KB), and the tags none."""
    libc = C.CDLL(None)
    libc.mallinfo2.restype = Mallinfo2
    rows = messy[1][2]
    before = libc.mallinfo2().uordblks
    s = songs(fw, rows, collect=1)
    grown = libc.mallinfo2().uordblks - before
    assert s.n == 64 and s.songs == 5000 and grown < 128 << 10, grown
    print(f'cd 2 (500 albums, 4 KB tags, 800-byte urls): {s.n} albums kept, heap +{grown // 1024} KB')
