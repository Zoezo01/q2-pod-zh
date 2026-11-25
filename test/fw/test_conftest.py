"""conftest.py's shared fakes themselves: what the other test files stand on."""
import ctypes as C
import os
import signal

import pytest
from conftest import Files, in_fork


def test_files_open_together_never_share_a_handle(fw):
    files = Files(fw, {'/a': b'first', '/b': b'second'})
    a, b = files.fopen(b'/a', b'rb'), files.fopen(b'/b', b'rb')
    files.fclose(a)  # the first closes while the second stays open
    c = files.fopen(b'/c', b'wb')
    assert c != b  # numbered by the count of open files alone, the third took the second's handle
    out = C.create_string_buffer(6)
    assert files.fread(out, 1, 6, b) == 6 and out.raw == b'second'
    assert files.fwrite(b'third', 1, 5, c) == 5
    files.fclose(c)
    files.fclose(b)
    assert files.files == {'/a': b'first', '/b': b'second', '/c': b'third'} and files.open == {}


def test_a_forked_test_cut_short_leaves_no_child(monkeypatch):
    """pytest-timeout's alarm raises in the parent's read of the child's pipe; the child never gets it."""
    pids, fork = [], os.fork
    monkeypatch.setattr(os, 'fork', lambda: pids.append(fork()) or pids[-1])
    monkeypatch.setattr(os, 'read', lambda *_: pytest.fail('Timeout'))
    try:
        with pytest.raises(pytest.fail.Exception, match='Timeout'):
            in_fork(signal.pause)()  # a test that never returns
        with pytest.raises(ProcessLookupError):  # killed and waited for: no such process
            os.kill(pids[0], 0)
    finally:
        try:
            os.kill(pids[0], signal.SIGKILL), os.waitpid(pids[0], 0)
        except OSError:
            pass
