"""host.py itself: the real firmware code runs, and stock and system calls can only reach stubs."""
import ctypes as C
import json

import host


def test_a_static_is_reachable(fw):
    le32 = fw.fn('xiph__le32', C.c_uint, C.c_char_p)
    assert le32(b'\x78\x56\x34\x12') == 0x12345678


def test_a_global_is_reachable(fw):
    find = fw.fn('csv_find', C.c_char_p, C.c_char_p, C.c_char_p)
    assert find(b'a,bc,d', b'bc') == b'bc,d'
    assert find(b'a,bc,d', b'b') is None


def test_each_load_starts_clean(library, tmp_path):
    (tmp_path/'a').mkdir()
    (tmp_path/'b').mkdir()
    first = host.Fw(library, tmp_path/'a')
    first.var('fw_log_n', C.c_int).value = 7
    assert host.Fw(library, tmp_path/'b').var('fw_log_n', C.c_int).value == 0


def test_stubs_cover_everything_with_effects(library):
    stubs = set(json.loads((library.parent/'stubs.json').read_text()))
    # What the firmware reaches for that would change this machine: never the real thing.
    assert {'fw_system', 'fw_fork', 'fw_unlink', 'fw_rename', 'fw_mkdir', 'fw_rmdir', 'fw_fopen', 'fw_execl'} <= stubs
    assert not stubs & host.PURE


def test_a_stub_records_and_a_mock_answers(fw):
    system = fw.fn('fw_system', C.c_int, C.c_char_p)
    assert system(b'reboot') == 0
    seen = []
    fw.mock('fw_system', lambda command, *rest: seen.append(C.string_at(command)) or 7)
    assert system(b'echo') == 7
    assert seen == [b'echo'] and [c[0] for c in fw.calls('fw_system')] == ['fw_system', 'fw_system']


def test_errno_is_the_library_s_own_cell_until_a_mock_answers(fw):
    """The firmware reads errno through libc's pointer after a failed call: a stub's 0 would be a NULL to read."""
    where = fw.fn('fw___errno_location', C.POINTER(C.c_int))
    assert where()[0] == 0
    where()[0] = 4  # as a test sets EINTR, or the firmware writes it
    assert fw.var('fw_errno', C.c_int).value == 4
    mine = C.c_int(11)
    fw.mock('fw___errno_location', lambda *_: C.addressof(mine))
    assert where()[0] == 11 and fw.var('fw_errno', C.c_int).value == 4
