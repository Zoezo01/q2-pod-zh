#!/usr/bin/env python3
"""Exercise actual C marker traversal and its generated predicates with real SQLite."""
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
        stub = tmp/'state.c'
        stub.write_text('volatile unsigned library_gen;\n')
        so = tmp/'scope.so'
        subprocess.run(['cc', '-shared', '-fPIC', '-pthread', '-DPEQ_HOST',
            f'-DPEQ_ROOT="{tmp}"', '-O1', '-Wall', '-Wextra', '-Werror',
            '-I', str(ROOT/'patch'), str(ROOT/'patch/library_scope.c'), str(stub),
            '-o', str(so)], check=True)
        lib = ctypes.CDLL(str(so))
        lib.library_scope_query.argtypes = [ctypes.c_char_p]
        lib.library_scope_query.restype = ctypes.c_void_p
        lib.library_scope_hidden.argtypes = [ctypes.c_char_p]
        lib.library_scope_hidden.restype = ctypes.c_int
        libc = ctypes.CDLL(None)
        libc.free.argtypes = [ctypes.c_void_p]
        generation = ctypes.c_uint.in_dll(lib, 'library_gen')
        def refresh(): generation.value += 2
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
        refresh(); check({1, 2, 4, 6, 7, 11, 13})
        grouped = db.execute(expand('select min(id), album from songtable where '+TOKEN+' group by album')).fetchall()
        assert dict((album, i) for i, album in grouped)['Shared'] == 0
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
        # Excessive nesting and rule sets fail open (the markers are ignored) and recover on refresh.
        deep = card/'Deep'
        (deep/Path(*(['nested'] * 65))).mkdir(parents=True)
        refresh(); check(set())
        shutil.rmtree(deep)
        refresh(); check({2, 4, 6, 7, 11})
        many = card/'Many'
        for i in range(600): marker('Many/'+str(i)+'x'*128)
        refresh(); check(set())
        shutil.rmtree(many)
        refresh(); check({2, 4, 6, 7, 11})
        assert original == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in original}
        print('library_scope: traversal, inheritance, exceptions, refresh, SQL quoting, grouping, playlist retention and unchanged files passed')

if __name__ == '__main__':
    main()
