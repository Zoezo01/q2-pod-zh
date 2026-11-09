#!/usr/bin/env python3
"""Exercise actual C marker traversal, its rules file and its generated predicates with real SQLite."""
import ctypes
import os
from pathlib import Path
import sqlite3
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TOKEN = '/*q2-library-scope*/1'

def main():
    with tempfile.TemporaryDirectory(prefix='q2-scope-') as directory:
        tmp = Path(directory)
        card = tmp/'mnt/mmc'
        card.mkdir(parents=True)
        (tmp/'mnt/data').mkdir()
        rules = tmp/'mnt/data/q2-library-scope'
        temporary = rules.with_name(rules.name + '.tmp')
        so = tmp/'scope.so'
        def build(*extra):
            subprocess.run(['cc', '-shared', '-fPIC', '-pthread', '-DPEQ_HOST',
                f'-DPEQ_ROOT="{tmp}"', '-O1', '-Wall', '-Wextra', '-Werror', *extra,
                '-I', str(ROOT/'patch'), str(ROOT/'patch/library_scope.c'),
                '-o', str(so)], check=True)
        build()
        libc = ctypes.CDLL(None)
        libc.free.argtypes = [ctypes.c_void_p]
        lib, boots = None, 0
        def boot():  # a fresh copy of the library, as after a reboot: nothing in memory
            nonlocal lib, boots
            boots += 1
            copy = tmp/f'scope{boots}.so'
            shutil.copy(so, copy)
            lib = ctypes.CDLL(str(copy))
            lib.library_scope_query.argtypes = [ctypes.c_char_p]
            lib.library_scope_query.restype = ctypes.c_void_p
            lib.library_scope_hidden.argtypes = [ctypes.c_char_p]
            lib.library_scope_hidden.restype = ctypes.c_int
        boot()
        def refresh(): lib.library_scope_scan()  # a library update
        def marker(folder, name='database.ignore'):
            target = card/folder/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
            return target
        def expand(sql):
            pointer = lib.library_scope_query(sql.encode())
            assert pointer
            try: return ctypes.string_at(pointer).decode()
            finally: libc.free(pointer)
        paths = ['Music/Album/a.flac', 'Singles/a.flac', 'Singles/Nested/b.flac',
            'Singles Extra/c.flac', 'Needledrop/a.flac', 'Needledrop/Keep/b.flac',
            'Needledrop/Keep/Hide/c.flac', "Odd ' %_ /a.flac", 'Odd X Z /a.flac',
            'Discovery/a.flac', 'Music/Discovery/a.flac', '日本語/é.flac',
            'Other/Singles/a.flac', 'singles/CASE.flac']
        urls = ['/mnt/mmc/'+p for p in paths]
        for p in paths:
            f = card/p; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b'unchanged')
        original = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in card.rglob('*.flac')}
        db = sqlite3.connect(':memory:')
        db.execute('create table songtable(id integer, fileurl text, album text)')
        db.executemany('insert into songtable values(?,?,?)',
            [(i, path, 'Shared' if i in (0, 1) else str(i)) for i, path in enumerate(urls)])
        def check(hidden):
            predicate = expand(TOKEN)
            visible = {r[0] for r in db.execute('select id from songtable where '+predicate)}
            assert visible == set(range(len(paths))) - set(hidden), (predicate, visible, hidden)
            for i, url in enumerate(urls):
                assert bool(lib.library_scope_hidden(url.encode())) == (i in hidden), url
            assert db.execute('select count(*) from songtable').fetchone()[0] == len(paths)
            # Explicit playlist/folder queries do not contain the placeholder and retain all rows.
            assert len(db.execute(expand('select * from songtable')).fetchall()) == len(paths)
            assert not lib.library_scope_hidden(b'/outside/Singles/a.flac')
            assert not lib.library_scope_hidden(None)
        check(set())  # Discovery is no longer implicitly hidden.
        singles = marker('Singles')
        marker('Needledrop')
        marker('Needledrop/Keep', 'database.unignore')
        marker('Needledrop/Keep/Hide')
        marker("Odd ' %_ ")
        marker('日本語')
        check(set())  # A library refresh, not per-row filesystem I/O, updates the snapshot.
        boot(); check(set())  # nor does a reboot: no scan has written the rules
        refresh(); check({1, 2, 4, 6, 7, 11, 13})
        grouped = db.execute(expand('select min(id), album from songtable where '+TOKEN+' group by album')).fetchall()
        assert dict((album, i) for i, album in grouped)['Shared'] == 0
        assert len(grouped) == 7  # what follows the token stays
        # Nearest marker wins, with unignore winning if both are in one directory.
        exception = marker('Singles/Nested', 'database.unignore')
        marker('Singles/Nested')
        refresh(); check({1, 4, 6, 7, 11, 13})
        exception.unlink(); singles.unlink()
        refresh(); check({2, 4, 6, 7, 11})
        # Root markers, nested exceptions and subsequent marker removal.
        root = marker('')
        refresh(); check(set(range(len(paths))) - {5})
        root.unlink(); refresh(); check({2, 4, 6, 7, 11})
        # A marker must be a regular file, not a directory with that name.
        (card/'Music/database.ignore').mkdir()
        refresh(); check({2, 4, 6, 7, 11})
        # Directory symlinks are not followed outside the card, including loops.
        os.symlink(card, card/'loop', target_is_directory=True)
        refresh(); check({2, 4, 6, 7, 11})
        # A marker above the card doesn't count.
        outside = card.parent/'database.ignore'
        outside.touch()
        refresh(); check({2, 4, 6, 7, 11})
        outside.unlink()
        # A folder that can't be opened is skipped.
        locked = card/'Locked'
        locked.mkdir(); locked.chmod(0)
        refresh(); check({2, 4, 6, 7, 11})
        locked.rmdir()
        # Excessive nesting and rule sets fail open (the markers are ignored) and recover on refresh.
        deep = card/'Deep'
        (deep/Path(*(['nested'] * 63))).mkdir(parents=True)  # 64 below the card: the deepest walked
        refresh(); check({2, 4, 6, 7, 11})
        (deep/Path(*(['nested'] * 64))).mkdir(parents=True)
        refresh(); check(set())
        assert not rules.exists() and not temporary.exists()
        shutil.rmtree(deep)
        refresh(); check({2, 4, 6, 7, 11})
        # The longest path walked is 765 bytes, PEQ_ROOT included.
        long = card/'Long'
        def folder(length):
            left = length - len(str(long))
            return long.joinpath('x' * (99 + left % 100), *['x' * 99] * (left // 100 - 1))
        assert len(str(folder(765))) == 765
        folder(765).mkdir(parents=True)
        refresh(); check({2, 4, 6, 7, 11})
        shutil.rmtree(long)
        folder(766).mkdir(parents=True)
        refresh(); check(set())
        assert not rules.exists()
        shutil.rmtree(long)
        refresh(); check({2, 4, 6, 7, 11})
        many = card/'Many'
        for i in range(600): marker('Many/'+str(i)+'x'*128)
        refresh(); check(set())
        assert not rules.exists() and not temporary.exists()
        shutil.rmtree(many)
        refresh(); check({2, 4, 6, 7, 11})
        lines = ['0/mnt/mmc/Needledrop', '0/mnt/mmc/Needledrop/Keep/Hide',
            "0/mnt/mmc/Odd ' %_ ", '0/mnt/mmc/Singles/Nested', '0/mnt/mmc/日本語', '1/mnt/mmc/Needledrop/Keep']
        assert sorted(rules.read_text().splitlines()) == lines
        text = rules.read_bytes()
        # The markers' SQL has a budget, each rule taking a share by its path's length. Found by search, not by the
        # share's formula: the most long-named folders whose markers a scan keeps, then the last of them named a byte
        # longer at a time, give the largest rules file a scan writes and, a byte on, the smallest it won't.
        budget, present = card/'Budget', set()
        def scan(folders):  # markers in just these folders, then a scan: the rules it writes, or None
            for name in present - set(folders): (budget/name/'database.ignore').unlink()
            for name in set(folders) - present: marker(f'Budget/{name}')
            present.clear(); present.update(folders)
            refresh()
            return rules.read_bytes() if rules.exists() else None
        def most(fits, low, high):  # the largest n in [low, high) that fits, given fits(low) and not fits(high)
            while high - low > 1:
                middle = (low + high) // 2
                if fits(middle): low = middle
                else: high = middle
            return low
        bulk = [f'F{i:03}'.ljust(80, 'y') for i in range(600)]
        assert scan([]) and not scan(bulk)
        count = most(lambda n: scan(bulk[:n]), 0, len(bulk))
        def last(length): return bulk[:count - 1] + [bulk[count - 1][:4].ljust(length, 'y')]
        length = most(lambda n: scan(last(n)), 80, 256)
        assert length < 255, 'the budget is out of one name\'s reach'
        # Whatever a scan writes, a read takes whole, after a reboot too, and neither's budget carries over.
        largest = scan(last(length))
        check({2, 4, 6, 7, 11})
        refresh(); check({2, 4, 6, 7, 11})
        boot(); check({2, 4, 6, 7, 11})
        # A byte past it, a scan writes no rules and a read refuses them: the markers are ignored, the whole
        # library shows.
        name = last(length)[-1].encode()
        assert scan(last(length + 1)) is None
        check(set())
        past = largest.replace(b'/' + name + b'\n', b'/' + name + b'y\n')
        assert past != largest
        rules.write_bytes(past); boot(); check(set())
        shutil.rmtree(budget)
        # Nothing rereads the file until a scan: the markers stay in memory.
        rules.write_bytes(text); boot(); check({2, 4, 6, 7, 11})
        rules.write_bytes(b''); check({2, 4, 6, 7, 11})
        # Nearest wins whatever the file's order, and library_scope_hidden reads the rules by itself.
        rules.write_bytes(b''.join(reversed(text.splitlines(True))))
        boot(); assert lib.library_scope_hidden(urls[2].encode())
        check({2, 4, 6, 7, 11})
        rules.write_bytes(text)
        # Updates leave no file open or memory held.
        class Mallinfo2(ctypes.Structure):
            _fields_ = [(name, ctypes.c_size_t) for name in
                'arena ordblks smblks hblks hblkhd usmblks fsmblks uordblks fordblks keepcost'.split()]
        libc.mallinfo2.restype = Mallinfo2
        def held():
            info = libc.mallinfo2()
            return info.uordblks + info.hblkhd
        def update():
            refresh()
            libc.free(lib.library_scope_query(TOKEN.encode()))
        update()
        files, memory = len(os.listdir('/proc/self/fd')), held()
        for _ in range(50): update()
        assert len(os.listdir('/proc/self/fd')) == files
        assert held() - memory < 8192, held() - memory
        check({2, 4, 6, 7, 11})
        # A reboot reads the rules file and walks nothing: a marker added since waits for a scan.
        discovery = marker('Discovery')
        boot(); check({2, 4, 6, 7, 11})
        # A cut or malformed rules file, or none, fails open, until the next scan.
        text = rules.read_bytes()
        for bad in (text[:-1], text[:text.index(b'\n') + 3], b'x' + text[1:], text[:1] + b'x' + text[1:], None):
            if bad is None: rules.unlink()
            else: rules.write_bytes(bad)
            boot(); check(set())
            refresh(); check({2, 4, 6, 7, 9, 11})
        # A scan that can't write its file leaves no markers.
        rules.unlink()
        temporary.mkdir()
        refresh(); check(set())
        assert not rules.exists()
        temporary.rmdir()
        refresh(); check({2, 4, 6, 7, 9, 11})
        discovery.unlink()
        # Hidden names (#154) work as the plain ones, an unignore's too, with any permissions.
        hidden = marker('Discovery', '.database.ignore')
        hidden.chmod(0o777)
        unhide = marker('Needledrop/Keep/Hide', '.database.unignore')
        refresh(); check({2, 4, 7, 9, 11})
        hidden.unlink(); unhide.unlink()
        # A filesystem that doesn't say what an entry is: folders are told by stat, symlinks still not followed.
        shim = tmp/'shim.h'
        shim.write_text('''#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
int fail_fflush, fail_fsync, fail_fclose, fail_calloc = -1;
static inline struct dirent *shim_readdir(DIR *d) { struct dirent *e = readdir(d); if (e) e->d_type = 0; return e; }
static inline int shim_fflush(FILE *f) { int r = fflush(f); return fail_fflush ? -1 : r; }
static inline int shim_fsync(int f) { int r = fsync(f); return fail_fsync ? -1 : r; }
static inline int shim_fclose(FILE *f) { int r = fclose(f); return fail_fclose ? -1 : r; }
static inline void *shim_calloc(size_t n, size_t s) { return !fail_calloc-- ? 0 : calloc(n, s); }
#define readdir shim_readdir
#define fflush shim_fflush
#define fsync shim_fsync
#define fclose shim_fclose
#define calloc shim_calloc
''')
        build('-include', str(shim))
        boot(); refresh(); check({2, 4, 6, 7, 11})
        assert sorted(rules.read_text().splitlines()) == lines
        def fail(name, value): ctypes.c_int.in_dll(lib, name).value = value
        # A write that fails leaves no markers, and the next scan brings them back.
        for name in ('fail_fflush', 'fail_fsync', 'fail_fclose'):
            fail(name, 1); refresh(); fail(name, 0)
            check(set())
            assert not rules.exists() and not temporary.exists()
            refresh(); check({2, 4, 6, 7, 11})
        # Out of memory at each allocation after a good read: the library shows whole, or the query says so.
        for k in range(9):  # the file's text, six rules, the SQL, then the expanded query
            refresh()
            libc.free(lib.library_scope_query(TOKEN.encode()))
            refresh()
            fail('fail_calloc', k)
            pointer = lib.library_scope_query(TOKEN.encode())
            fail('fail_calloc', -1)
            assert bool(pointer) == (k < 8), k
            libc.free(pointer)
            if k < 8: check(set())
            else: check({2, 4, 6, 7, 11})
        assert original =={p: (p.read_bytes(), p.stat().st_mtime_ns) for p in original}
        print('library_scope: traversal, rules file across reboots, cut or missing rules, inheritance, exceptions,'
              ' refresh, limits, leaks, failed writes and allocations, SQL quoting, grouping, playlist retention'
              ' and unchanged files passed')

if __name__ == '__main__':
    main()
