#!/usr/bin/env python3
"""Compile the player with the FLAC fast paths and with --stock-flac's stock paths, and check each build."""
import argparse
import pathlib
import struct
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
from build import fileoff, symbols
from peq import patch_player
from flac import EXPERIMENTS, EXPORTS, POINTERS, SYNC_CALLS, patch_words


def verify(stock, out, baseline=None):
    raw = stock.read_bytes()
    all_addresses = set(SYNC_CALLS)
    for pointers in POINTERS.values():
        for _, hi, _, lo, _ in pointers:
            all_addresses.update((hi, lo))
    for selected in (EXPERIMENTS, ()):  # the default, then --stock-flac
        directory = out / ('-'.join(selected) or 'stock')
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
        for address in (0x42b710, 0x5baedc, 0x5bb05c, 0x5b83a0, 0x5b9238, 0x5b90c0, 0x5bad5c):
            off = fileoff(raw, address)
            assert new[off:off+32] == raw[off:off+32], 'stock fallback changed'
        if not selected and baseline:
            assert new == baseline.read_bytes(), 'stock-path player differs from baseline'
        print('PASS', selected or ('stock',), flush=True)
    print('The fast paths, the stock paths and the original fallbacks passed.')


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
