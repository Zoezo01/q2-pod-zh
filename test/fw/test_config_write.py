"""Settings writer: the real ringnav_config_write (system.c) over stand-ins for stock's writer and the flash."""
import ctypes as C

import pytest

INI = '/mnt/data/config.ini'
NEW = INI + '.new'
WHOLE = b'[SYSSET]\nBLUETOOTH=0\n' + b''.join(b'KEY%d=%d\n' % (i, i) for i in range(60))


def name(path):
    return C.string_at(path).decode()


class Flash:
    """The flash's files as a dict, and stock's toolsWriteConfig past its hook: it rewrites the file it is given in
    place, as stock does, or leaves it as `fault` says. `during` is the flash as stock's writer found it."""
    def __init__(self, fw, files, fault=None):
        self.files, self.fds, self.depth, self.events, self.fault = dict(files), {}, 0, [], fault
        self.during = {}
        self.save = fw.fn('ringnav_config_write', C.c_int, C.c_char_p, C.c_char_p, C.c_char_p, C.c_char_p)
        fw.mock('fw_pthread_mutex_lock', lambda *_: self.lock(0))
        fw.mock('fw_pthread_mutex_unlock', lambda *_: self.lock(1))
        fw.mock('fw_access', lambda path, *_: 0 if name(path) in self.files else -1)
        fw.mock('toolsCopyFile', self.copy)
        fw.mock('stock_config_write_trampoline', self.stock)
        fw.mock('fw_fopen', self.open)
        fw.mock('fw_fileno', lambda f, *_: f)
        fw.mock('fw_fsync', self.sync)
        fw.mock('fw_fseek', lambda f, *_: -(self.fds[f] not in self.files))  # a folder has no size
        fw.mock('fw_ftell', lambda f, *_: len(self.files[self.fds[f]]))
        fw.mock('fw_fclose', lambda f, *_: self.fds.pop(f) and 0)
        fw.mock('fw_rename', self.rename)
        fw.mock('fw_unlink', lambda path, *_: self.files.pop(name(path), None) and 0)

    def lock(self, held):
        assert self.depth == held
        self.depth = 1 - held
        return 0

    def copy(self, src, dst, *_):
        src, dst = name(src), name(dst)
        self.events.append(('copy', src, dst))
        if self.fault == 'copy' or src not in self.files:
            return -1
        self.files[dst] = self.files[src]
        return len(self.files[src])

    def stock(self, path, _section, key, value, *_):
        assert self.depth == 1
        path, key = name(path), C.string_at(key)
        self.events.append(('stock', path))
        self.during = dict(self.files)
        if self.fault == 'stock':
            return -1
        old = self.files.get(path, b'')
        kept = [line for line in old.splitlines(keepends=True) if not line.startswith(key + b'=')]
        new = b''.join(kept) + key + b'=' + C.string_at(value) + b'\n'
        self.files[path] = new[:len(old) - self.fault[1]] if isinstance(self.fault, tuple) else new
        return 0

    def open(self, path, *_):
        path = name(path)
        if self.fault == 'open' or not any(f == path or f.startswith(path + '/') for f in self.files):
            return 0
        self.fds[len(self.fds) + 5] = path
        return len(self.fds) + 4

    def sync(self, fd, *_):
        self.events.append(('sync', self.fds[fd]))
        return -(self.fault == 'sync')

    def rename(self, old, new, *_):
        old, new = name(old), name(new)
        self.events.append(('rename', old, new))
        if self.fault == 'rename':
            return -1
        self.files[new] = self.files.pop(old)
        return 0


def test_a_save_rewrites_a_copy_and_renames_it_over_the_file(fw):
    """Stock's writer truncates the file it is given and writes it again: it is given the copy, so the file is whole
    whenever it is cut off. The copy is on the flash before the rename, and the folder after."""
    flash = Flash(fw, {INI: WHOLE})
    assert flash.save(INI.encode(), b'SYSSET', b'BLUETOOTH', b'1') == 0
    assert flash.during == {INI: WHOLE, NEW: WHOLE}
    assert flash.files == {INI: WHOLE.replace(b'BLUETOOTH=0\n', b'') + b'BLUETOOTH=1\n'}
    assert flash.events == [('copy', INI, NEW), ('stock', NEW), ('sync', NEW), ('rename', NEW, INI),
                            ('sync', '/mnt/data')]
    assert flash.depth == 0 and not flash.fds


@pytest.mark.parametrize('fault', ['copy', 'stock', 'open', 'sync', 'rename', ('cut', len(WHOLE)), ('cut', 257)])
def test_a_save_that_fails_leaves_the_file_as_it_was(fw, fault):
    """A copy that can't be made, stock's writer failing, a copy that isn't on the flash, comes out empty or is cut
    short by more than a line (a full /tmp: stock checks no write), or a rename refused."""
    flash = Flash(fw, {INI: WHOLE}, fault)
    assert flash.save(INI.encode(), b'SYSSET', b'BLUETOOTH', b'1') == -1
    assert flash.files == {INI: WHOLE} and flash.depth == 0 and not flash.fds
    assert all(event[1] != INI for event in flash.events if event[0] == 'stock')  # never stock's truncate on the file


def test_a_save_may_shorten_the_file_by_a_line(fw):
    """A long value replaced by a short one: 256 bytes less, toolsWriteConfig's longest line, is still a whole file."""
    flash = Flash(fw, {INI: WHOLE}, ('cut', 256))
    assert flash.save(INI.encode(), b'SYSSET', b'BLUETOOTH', b'1') == 0
    assert len(flash.files[INI]) == len(WHOLE) - 256 and NEW not in flash.files


def test_an_empty_file_takes_a_save(fw):
    """A config.ini an earlier firmware left empty fills again from its first save."""
    flash = Flash(fw, {INI: b''})
    assert flash.save(INI.encode(), b'SYSSET', b'BLUETOOTH', b'1') == 0
    assert flash.files == {INI: b'BLUETOOTH=1\n'}


@pytest.mark.parametrize('result', [0, -1])
def test_a_file_not_there_yet_is_stocks_to_create(fw, result):
    flash = Flash(fw, {}, 'stock' if result else None)
    assert flash.save(INI.encode(), b'SYSSET', b'BLUETOOTH', b'1') == result
    assert flash.events == [('stock', INI)] and flash.depth == 0
    assert flash.files == ({} if result else {INI: b'BLUETOOTH=1\n'})


def test_a_name_too_long_for_the_copys_is_stocks_too(fw):
    path = '/mnt/data/' + 'x' * 290
    flash = Flash(fw, {path: WHOLE})
    assert flash.save(path.encode(), b'SYSSET', b'BLUETOOTH', b'1') == 0
    assert flash.events == [('stock', path)]
