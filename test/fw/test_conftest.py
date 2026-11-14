"""conftest.py's shared fakes themselves: what the other test files stand on."""
import ctypes as C

from conftest import Files


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
