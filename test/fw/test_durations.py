"""Shared duration service: real C, fake card/TagLib/thread boundary, no stock ZIP needed."""
import ctypes as C
import random
import struct

import pytest

from conftest import Files
from test_nav_library_pages import Config, Library, Probe, LENGTHS, lengths, song, ui  # noqa: F401


class Ds(C.Structure):
    _fields_ = [('tab', C.c_void_p), ('jobs', C.c_void_p),
                *((k, C.c_uint) for k in ('n', 'total', 'read', 'writable', 'timer_at', 'gen', 'seen')),
                ('thread', C.c_ulong),
                *((k, C.c_int) for k in ('running', 'done', 'cancel', 'exited', 'mounted'))]


def seconds(fw, record):
    return fw.fn('duration_seconds', C.c_int, C.c_void_p)(record)


def start(fw, ui, records):
    fw.fn('pod_settings', None)()
    return fw.fn('durations_start', C.c_int, C.c_void_p)(ui.deques.new(records))


def finish(fw, probe):
    probe.run()
    assert fw.fn('durations_busy', C.c_int, C.c_void_p, C.c_void_p)(None, None) == 0


def test_cache_uses_exact_paths_and_no_file_probes_on_lookup(fw, song):
    Files(fw, {LENGTHS: lengths((b'/m/a', 225), (b'/m/b', 3601))})
    assert [seconds(fw, song(p)) for p in (b'/m/a', b'/m/b', b'/m/c')] == [225, 3601, -1]
    assert not fw.calls('taglib_file_new') and not fw.calls('fw___xstat')


@pytest.mark.parametrize('cue,end,want', [(0, 0, 1000), (30, 90, 60), (900, 0, 100), (1200, 0, 0), (-1, 0, 1000)])
def test_cue_duration_is_relative_to_the_track(fw, song, cue, end, want):
    Files(fw, {LENGTHS: lengths((b'/m/a', 1000))})
    assert seconds(fw, song(b'/m/a', cue=cue, end=end)) == want


@pytest.mark.parametrize('sec,want', [(0, b''), (225, b'3:45'), (3599, b'59:59'), (3600, b'1:00:00'), (36001, b'10:00:01')])
def test_duration_labels(fw, song, sec, want):
    Files(fw, {LENGTHS: lengths((b'/m/a', sec))})
    out = C.create_string_buffer(24)
    fw.fn('duration_text', C.c_char_p, C.c_char_p, C.c_uint, C.c_void_p)(out, len(out), song(b'/m/a'))
    assert out.value == want


@pytest.mark.parametrize('data', [b'', b'old hash-only cache', b'Q2DUR1\0\0\1',
                                  b'Q2DUR1\0\0' + struct.pack('<5I', 4097, 1, 1, 1, 1),
                                  lengths((b'/m/a', 1))[:-1], lengths((b'/m/a', 1), (b'/m/a', 2)),
                                  lengths((b'/m/a', 0xffffffff))])
def test_bad_cache_is_rejected_whole(fw, song, data):
    Files(fw, {LENGTHS: data})
    assert seconds(fw, song(b'/m/a')) == -1
    assert fw.var('durations__ds', Ds).n == 0


def test_missing_cache_and_null_song(fw, song):
    Files(fw)
    assert seconds(fw, None) == -1 and seconds(fw, song(b'/m/a')) == -1
    assert fw.var('durations__ds', Ds).read == 1


def test_arbitrary_cache_bytes_are_safe(fw, song):
    files, rng = Files(fw), random.Random(0x1e4)
    state = fw.var('durations__ds', Ds)
    for _ in range(800):
        files.files[LENGTHS] = rng.randbytes(rng.randrange(128))
        state.read = 0
        assert seconds(fw, song(b'/m/a')) == -1 and state.n == 0


def test_toggle_defaults_off_and_blocks_scanning(fw, ui, song):
    Files(fw)
    Config(fw)
    assert not start(fw, ui, [song(b'/m/a')])
    assert not fw.calls('fw_pthread_create')


def test_unchanged_file_is_not_reparsed_or_rewritten(fw, ui, song):
    files = Files(fw, {LENGTHS: lengths((b'/m/a', 225))})
    probe = Probe(fw, {b'/m/a': 999})
    assert start(fw, ui, [song(b'/m/a')])
    finish(fw, probe)
    assert seconds(fw, song(b'/m/a')) == 225 and probe.opened == []
    assert not fw.calls('fw_fwrite') and files.files[LENGTHS] == lengths((b'/m/a', 225))


@pytest.mark.parametrize('stamp', [(1001, 2000, 0), (1000, 2001, 0), (1000, 2000, 1)])
def test_changed_file_replaces_old_duration(fw, ui, song, stamp):
    Files(fw, {LENGTHS: lengths((b'/m/a', 225))})
    probe = Probe(fw, {b'/m/a': 320})
    probe.stamps[b'/m/a'] = stamp
    assert start(fw, ui, [song(b'/m/a')])
    finish(fw, probe)
    assert probe.opened == [b'/m/a'] and seconds(fw, song(b'/m/a')) == 320


def test_duplicate_cue_paths_are_probed_once_and_jobs_own_paths(fw, ui, song):
    Files(fw)
    probe = Probe(fw, {b'/m/a': 900})
    a, b = song(b'/m/a', cue=0), song(b'/m/a', cue=500)
    records = ui.deques.new([a, b])
    fw.fn('pod_settings', None)()
    assert fw.fn('durations_start', C.c_int, C.c_void_p)(records)
    del ui.deques.lists[records]  # caller's snapshot can go before the worker runs
    finish(fw, probe)
    assert probe.opened == [b'/m/a'] and [seconds(fw, r) for r in (a, b)] == [900, 400]


def test_one_shared_worker_and_results_published_only_after_join(fw, ui, song):
    Files(fw)
    probe = Probe(fw, {b'/m/a': 225})
    a = song(b'/m/a')
    assert start(fw, ui, [a]) and start(fw, ui, [a])
    assert len(probe.threads) == 1 and seconds(fw, a) == -1
    probe.run()
    assert seconds(fw, a) == -1
    fw.fn('durations_busy', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert seconds(fw, a) == 225 and len(fw.calls('fw_pthread_join')) == 1


def test_cancel_preserves_cached_records_and_does_not_join_a_running_worker(fw, ui, song):
    files = Files(fw, {LENGTHS: lengths((b'/m/a', 225))})
    probe = Probe(fw, {b'/m/b': 900})
    assert start(fw, ui, [song(b'/m/a'), song(b'/m/b')])
    fw.fn('durations_cancel', None)()
    assert fw.fn('durations_busy', C.c_int, C.c_void_p, C.c_void_p)(None, None) == 1
    assert not fw.calls('fw_pthread_join')
    finish(fw, probe)
    assert probe.opened == [] and files.files[LENGTHS] == lengths((b'/m/a', 225))


def test_failed_stat_discards_stale_duration_and_can_be_retried(fw, ui, song):
    Files(fw, {LENGTHS: lengths((b'/m/a', 225))})
    probe = Probe(fw, {b'/m/a': 500})
    probe.stamps[b'/m/a'] = None
    a = song(b'/m/a')
    assert start(fw, ui, [a])
    finish(fw, probe)
    assert seconds(fw, a) == -1 and probe.opened == []
    probe.stamps[b'/m/a'] = (1000, 2000, 0)
    assert start(fw, ui, [a])
    finish(fw, probe)
    assert seconds(fw, a) == 500


def test_removed_files_are_pruned_after_a_complete_scan(fw, ui, song):
    files = Files(fw, {LENGTHS: lengths((b'/m/a', 225), (b'/m/deleted', 900))})
    probe = Probe(fw, {b'/m/a': 225})
    assert start(fw, ui, [song(b'/m/a')])
    finish(fw, probe)
    assert seconds(fw, song(b'/m/deleted')) == -1 and files.files[LENGTHS] == lengths((b'/m/a', 225))
