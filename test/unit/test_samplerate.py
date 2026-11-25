"""patch/samplerate.c on the host: Show Sample Rate's reading of an ALSA playback PCM's hw_params, its DoP and
native DSD rules, and the status bar's text. Built under ~/.cache (tmpfs /tmp is RAM) and removed after."""
import ctypes as C
import os
import pathlib
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class Out(C.Structure):
    _fields_ = [('rate', C.c_uint), ('bits', C.c_uint), ('dsd', C.c_uint)]


class Lib(C.CDLL):
    root: pathlib.Path  # PEQ_ROOT, the fake /proc and /mnt the build reads


@pytest.fixture(scope='module')
def lib():
    cache = pathlib.Path(os.environ.get('XDG_CACHE_HOME') or pathlib.Path.home()/'.cache')
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='q2-rate-', dir=cache) as tmp:
        d = pathlib.Path(tmp)
        subprocess.run(['clang', '-shared', '-fPIC', '-DPEQ_HOST', f'-DPEQ_ROOT="{d}/root"', '-Wall', '-Wextra',
                        '-Werror', str(ROOT/'patch/samplerate.c'), '-o', str(d/'rate.so')], check=True)
        so = Lib(str(d/'rate.so'))
        so.sr_now.argtypes = [C.c_int, C.c_char_p, C.c_uint]
        so.sr_forget.argtypes = []
        so.root = d/'root'
        so.sr_parse_hw_params.argtypes = [C.c_char_p, C.POINTER(Out)]
        so.sr_dop.argtypes = [C.POINTER(Out), C.c_int]
        so.sr_format.argtypes = [C.POINTER(Out), C.c_int, C.c_char_p, C.c_uint]
        yield so


def hw(fmt, rate):
    return (f'access: RW_INTERLEAVED\nformat: {fmt}\nsubformat: STD\nchannels: 2\n'
            f'rate: {rate} ({rate}/1)\nperiod_size: 1024\nbuffer_size: 4096\n').encode()


def parse(lib, text):
    o = Out()
    ok = lib.sr_parse_hw_params(text, C.byref(o))
    return ok, (o.rate, o.bits, o.dsd)


def text(lib, rate, bits=0, dsd=0, mode=1):
    o, buf = Out(rate, bits, dsd), C.create_string_buffer(32)
    n = lib.sr_format(C.byref(o), mode, buf, len(buf))
    assert n == len(buf.value)
    return buf.value.decode()


@pytest.mark.parametrize('fmt,bits', [('S16_LE', 16), ('S24_LE', 24), ('S24_3LE', 24), ('S32_LE', 32),
                                      ('FLOAT_LE', 0), ('MU_LAW', 0)])
def test_a_pcm_reads_its_rate_and_bits(lib, fmt, bits):
    assert parse(lib, hw(fmt, 44100)) == (1, (44100, bits, 0))


def test_a_closed_or_unreadable_pcm_is_none(lib):
    assert parse(lib, b'closed\n')[0] == 0
    assert parse(lib, b'')[0] == 0
    assert parse(lib, b'format: S16_LE\n')[0] == 0  # no rate


@pytest.mark.parametrize('fmt,rate,dsd', [('DSD_U32_BE', 88200, 64), ('DSD_U8', 352800, 64),
                                          ('DSD_U16_LE', 176400, 64), ('DSD_U32_BE', 176400, 128),
                                          ('DSD_U32_LE', 352800, 256)])
def test_native_dsd_is_its_multiple(lib, fmt, rate, dsd):
    assert parse(lib, hw(fmt, rate)) == (1, (rate, 0, dsd))


@pytest.mark.parametrize('rate,dsd_song,dsd', [(176400, 1, 64), (352800, 1, 128), (705600, 1, 256),
                                               (176400, 0, 0), (88200, 1, 0)])
def test_dop_is_a_dsd_song_on_a_fast_enough_pcm(lib, rate, dsd_song, dsd):
    o = Out(rate, 32, 0)
    lib.sr_dop(C.byref(o), dsd_song)
    assert o.dsd == dsd


def test_dop_needs_24_bits(lib):
    o = Out(176400, 16, 0)
    lib.sr_dop(C.byref(o), 1)
    assert o.dsd == 0


@pytest.mark.parametrize('rate,want', [(44100, '44.1k'), (48000, '48k'), (22050, '22.05k'), (11025, '11.025k'),
                                       (8000, '8k'), (352800, '352.8k'), (768000, '768k'), (192000, '192k')])
def test_rate_alone(lib, rate, want):
    assert text(lib, rate, 24) == want


def test_the_rate_alone_whatever_the_bits(lib):
    assert text(lib, 44100, 16) == '44.1k' and text(lib, 96000, 24) == '96k' and text(lib, 96000, 0) == '96k'


def test_dsd_is_its_multiple(lib):
    assert text(lib, 88200, 0, 64) == 'DSD64'
    assert text(lib, 352800, 24, 128) == 'DSD128'


def test_off_or_no_rate_is_empty(lib):
    assert text(lib, 44100, 16, mode=0) == ''  # Off
    assert text(lib, 0, 16, mode=1) == ''


def test_a_small_buffer_is_cut_not_overrun(lib):
    o, buf = Out(352800, 24, 0), C.create_string_buffer(b'\xff' * 8, 8)
    lib.sr_format(C.byref(o), 1, buf, 4)
    assert buf.raw[3] == 0 and buf.raw[4:] == b'\xff' * 4


def pcm(root, card, dev, state, params):
    d = root/f'proc/asound/card{card}/pcm{dev}p/sub0'
    d.mkdir(parents=True, exist_ok=True)
    (d/'hw_params').write_bytes(params)
    (d/'status').write_text(f'state: {state}\n' if state else 'closed\n')


@pytest.fixture
def root(lib):
    import shutil
    shutil.rmtree(lib.root, ignore_errors=True)
    lib.sr_forget()
    yield lib.root
    shutil.rmtree(lib.root, ignore_errors=True)


def now(lib, mode=1):
    buf = C.create_string_buffer(32)
    n = lib.sr_now(mode, buf, len(buf))
    assert n == len(buf.value)
    return buf.value.decode()


def test_nothing_open_shows_nothing(lib, root):
    pcm(root, 0, 0, '', b'closed\n')
    assert now(lib) == ''


def test_a_usb_dac_beside_a_closed_headphone_dac(lib, root):
    pcm(root, 0, 0, '', b'closed\n')
    pcm(root, 1, 0, 'RUNNING', hw('S24_3LE', 96000))
    assert now(lib) == '96k'


def test_the_running_pcm_wins_over_one_only_prepared(lib, root):
    pcm(root, 0, 0, 'PREPARED', hw('S16_LE', 44100))
    pcm(root, 1, 0, 'RUNNING', hw('S32_LE', 192000))
    assert now(lib) == '192k'


def test_a_paused_pcm_keeps_its_rate(lib, root):
    pcm(root, 0, 0, 'PAUSED', hw('S24_LE', 48000))
    assert now(lib) == '48k'


def test_a_read_is_kept_half_a_second(lib, root):
    pcm(root, 0, 0, 'RUNNING', hw('S16_LE', 44100))
    assert now(lib) == '44.1k'
    pcm(root, 0, 0, 'RUNNING', hw('S16_LE', 48000))
    assert now(lib) == '44.1k'  # cached
    lib.sr_forget()
    assert now(lib) == '48k'


def test_off_reads_nothing(lib, root):
    pcm(root, 0, 0, 'RUNNING', hw('S16_LE', 44100))
    assert now(lib, 0) == ''

