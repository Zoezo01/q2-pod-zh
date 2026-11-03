#!/usr/bin/env python3
"""Compile every FLAC selection and check hook isolation and the release gate."""
import argparse
import itertools
import pathlib
import struct
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
from build import build, fileoff, symbols
from peq import patch_player
from flac import EXPERIMENTS, EXPORTS, POINTERS, SYNC_CALLS, patch_words, selection


def verify(stock, out, baseline=None):
    for invalid in ('sync,sync', 'all', 'lpc,', ',sync', 'sync, output'):
        try:
            selection(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(('accepted invalid selection', invalid))
    assert selection('output,sync') == ('sync', 'output') and selection('') == ()
    refused = out / 'must-not-be-created'
    try:
        build(out/'absent.zip', refused, out/'absent.jpg', flac_experiments='lpc')
    except ValueError as e:
        assert 'require --dev' in str(e)
    else:
        raise AssertionError('release accepted experiment')
    assert not refused.exists(), 'release rejection wrote output'
    raw = stock.read_bytes()
    all_addresses = set(SYNC_CALLS)
    for pointers in POINTERS.values():
        for _, hi, _, lo, _ in pointers:
            all_addresses.update((hi, lo))
    for bits in itertools.product((False, True), repeat=3):
        selected = tuple(n for n, on in zip(EXPERIMENTS, bits) if on)
        directory = out / ('-'.join(selected) or 'default')
        manifest = patch_player(raw, directory, selected)
        new = (directory/'hciplayer').read_bytes()
        syms = symbols(directory/'peq.elf')
        expected = {a: patched for a, _, patched in patch_words(selected, syms)}
        assert manifest.get('flac_experiments', []) == list(selected)
        for name, exports in EXPORTS.items():
            for symbol in exports:
                assert (symbol in syms) == (name in selected), (selected, symbol)
        for address in all_addresses:
            off = fileoff(raw, address)
            old = struct.unpack_from('<I', raw, off)[0]
            assert struct.unpack_from('<I', new, off)[0] == expected.get(address, old)
        for address in (0x42b6b0, 0x5ba9bc, 0x5bab3c, 0x5b7e80, 0x5b8d18, 0x5b8ba0, 0x5ba83c):
            off = fileoff(raw, address)
            assert new[off:off+32] == raw[off:off+32], 'stock fallback changed'
        if not selected and baseline:
            assert new == baseline.read_bytes(), 'default player differs from baseline'
        print('PASS', selected or ('default',), flush=True)
    print('All eight build selections, original fallbacks, invalid names and release gate passed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=pathlib.Path)
    parser.add_argument('--out', type=pathlib.Path)
    parser.add_argument('--baseline', type=pathlib.Path)
    args = parser.parse_args()
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        verify(args.stock, args.out, args.baseline)
    else:
        with tempfile.TemporaryDirectory(prefix='q2-flac-build-') as directory:
            verify(args.stock, pathlib.Path(directory), args.baseline)
