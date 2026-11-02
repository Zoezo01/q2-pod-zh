#!/usr/bin/env python3
"""Development guard, exact module ABI and notifier/default package checks."""
import argparse
import hashlib
import io
import json
import pathlib
import struct
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
from build import build, source_sha256
from aic import Elf, MODULE_PATH, PATCH_POLICY, STOCK_SHA256, patch_module

def sha(data):
    return hashlib.sha256(data).hexdigest()

with tempfile.TemporaryDirectory() as directory:
    root = pathlib.Path(directory)
    try:
        build(root / 'missing.zip', root / 'output', root / 'missing.jpg',
              aic_idle_wait=True)
    except ValueError as exc:
        assert str(exc) == '--aic-idle-wait requires --dev'
    else:
        raise AssertionError('Notifier experiment accepted without --dev')
    assert not (root / 'output').exists()

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('stockfs', type=pathlib.Path)
ap.add_argument('builds', nargs='*', type=pathlib.Path)
args = ap.parse_args()
stock = (args.stockfs / MODULE_PATH).read_bytes()
assert sha(stock) == STOCK_SHA256
patched = patch_module(stock)
assert patched == patch_module(stock), 'Module patch is not reproducible'
before, after = Elf(stock), Elf(patched)

def modinfo(elf):
    return dict(part.split(b'=', 1) for part in
                bytes(elf.by_name['.modinfo']['data']).split(b'\0') if b'=' in part)

assert before.header[7] == after.header[7]
metadata = modinfo(after)
assert metadata.pop(b'q2_aic_notifier') == PATCH_POLICY.encode()
assert modinfo(before) == metadata
for name in ('.MIPS.abiflags', '.gnu.linkonce.this_module'):
    assert before.by_name[name]['data'] == after.by_name[name]['data'], name


def canonical_data(elf, name):
    """Compare stored values plus what each R_MIPS_32 pointer resolves to.

    ld.lld -r reorders SHF_MERGE|SHF_STRINGS pools, changing pointer addends
    without changing their string contents. Other relocated pointers must keep
    the same target section and offset (symbol value plus implicit addend).
    """
    section = elf.by_name[name]
    index = elf.sections.index(section)
    data = bytearray(section['data'])
    pointers = {}
    for reloc in elf.sections:
        if reloc['type'] != 9 or reloc['info'] != index:
            continue
        for offset, info in struct.iter_unpack('<II', reloc['data']):
            assert info & 255 == 2, ('Expected R_MIPS_32', name, offset)
            assert offset % 4 == 0 and offset + 4 <= len(data), (name, offset)
            assert offset not in pointers, ('Overlapping relocation', name, offset)
            symbol = elf.symbols[info >> 8]
            addend = struct.unpack_from('<I', section['data'], offset)[0]
            target = (symbol['value'] + addend) & 0xffffffff
            target_index = symbol['section']
            if target_index == 0:
                assert symbol['name'], ('Unnamed undefined pointer', name, offset)
                value = ('undefined', symbol['name'], addend)
            elif target_index == 0xfff1:  # SHN_ABS
                value = ('absolute', target)
            else:
                assert 0 < target_index < len(elf.sections), target_index
                destination = elf.sections[target_index]
                if destination['flags'] & 0x20:  # SHF_STRINGS
                    assert destination['flags'] & 0x10 and destination['entsize'] == 1
                    pool = bytes(destination['data'])
                    assert target < len(pool), ('String pointer out of range', name, offset)
                    end = pool.find(b'\0', target)
                    assert end >= target, ('Unterminated string', name, offset)
                    value = ('string', destination['name'], pool[target:end + 1])
                else:
                    value = ('section', destination['name'], target)
            pointers[offset] = value
            data[offset:offset + 4] = bytes(4)
    layout = tuple(section[key] for key in ('type', 'flags', 'align', 'entsize'))
    return layout, bytes(data), pointers


for name in ('.data', '.rodata', '.gnu.linkonce.this_module'):
    assert canonical_data(before, name) == canonical_data(after, name), name

# Negative controls: normalization must not hide a changed string, a retargeted
# callback, a scalar hardware constraint, or missing/wrong relocation metadata.
def rejects_data_change(name, change):
    damaged = Elf(patched)
    change(damaged)
    try:
        assert canonical_data(before, name) == canonical_data(damaged, name)
    except AssertionError:
        return
    raise AssertionError('Data comparison accepted corruption in ' + name)


def change_pointer(elf, name, offset):
    data = elf.by_name[name]['data']
    value = struct.unpack_from('<I', data, offset)[0]
    struct.pack_into('<I', data, offset, value + 4)


def change_string(elf):
    # This pointer names the actual platform device, not an unused pool entry.
    section = elf.by_name['.data']
    index = elf.sections.index(section)
    reloc = next(r for r in elf.sections if r['type'] == 9 and r['info'] == index)
    info = next(info for offset, info in struct.iter_unpack('<II', reloc['data'])
                if offset == 0)
    symbol = elf.symbols[info >> 8]
    target = (symbol['value'] + struct.unpack_from('<I', section['data'])[0]) & 0xffffffff
    elf.sections[symbol['section']]['data'][target] ^= 1


def change_relocation(elf, remove=False):
    index = elf.sections.index(elf.by_name['.data'])
    reloc = next(r for r in elf.sections if r['type'] == 9 and r['info'] == index)
    if remove:
        del reloc['data'][:8]
    else:
        info = struct.unpack_from('<I', reloc['data'], 4)[0]
        struct.pack_into('<I', reloc['data'], 4, (info & ~255) | 4)


rejects_data_change('.data', lambda e: change_pointer(e, '.data', 0))
rejects_data_change('.data', change_string)
rejects_data_change('.data', lambda e: change_pointer(e, '.data', 0x2fc))
rejects_data_change('.rodata', lambda e: e.by_name['.rodata']['data'].__setitem__(
    0x130, e.by_name['.rodata']['data'][0x130] ^ 1))
rejects_data_change('.data', change_relocation)
rejects_data_change('.data', lambda e: change_relocation(e, remove=True))
assert before.by_name['.bss']['size'] == after.by_name['.bss']['size']
assert before.by_name['.note.gnu.build-id']['data'] != after.by_name['.note.gnu.build-id']['data']
# Undefined symbol names are kernel/module dependencies. No new ABI is introduced.
def undefined(elf):
    return {s['name'] for s in elf.symbols if s['section'] == 0 and s['name']}
assert undefined(before) == undefined(after)
for section in after.sections:
    if section['type'] == 9:
        for offset, info in struct.iter_unpack('<II', section['data']):
            assert not (info & 255 == 4 and after.symbols[info >> 8]['section'] == 0), (
                'external R_MIPS_26 cannot cross module/kernel regions', section['name'], offset)
print('Development guard, deterministic module, unchanged ABI/data targets, six corruption controls and long calls passed.')

for directory in args.builds:
    manifest = json.loads((directory / 'manifest.json').read_text())
    assert manifest['source_sha256'] == source_sha256(), 'Build uses stale source'
    squash = directory / 'rootfs.squashfs'
    def cat(path):
        return subprocess.check_output(['unsquashfs', '-cat', str(squash), path])
    actual = cat(MODULE_PATH)
    if 'aic_notifier' in manifest:
        assert manifest['dev']
        assert actual == patched == (directory / 'soc_aic.ko').read_bytes()
        assert manifest['aic_notifier'] == dict(
            policy=PATCH_POLICY, path=MODULE_PATH,
            original_sha256=STOCK_SHA256, sha256=sha(patched))
    else:
        assert actual == stock, 'Default build changed soc_aic.ko'
    # This independent experiment must not include the other clock-gating PR.
    for path in ('module_driver/soc_audio_dma.ko', 'module_driver/soc_pwm.ko',
                 'module_driver/soc_mcu.ko', 'module_driver/soc_rotator.ko',
                 'usr/data/libmcu-bare.bin'):
        assert cat(path) == (args.stockfs / path).read_bytes(), path
    assert sha(squash.read_bytes()) == manifest['rootfs_sha256']
    assert squash.stat().st_size <= (directory / 'stock.squashfs').stat().st_size
    update = (directory / 'update.tar').read_bytes()
    assert sha(update) == manifest['update_sha256']
    with tarfile.open(fileobj=io.BytesIO(update)) as archive:
        info = archive.extractfile('firmware_v20.info').read().decode().splitlines()
        assert info[:2] == ['Shanling Q2', manifest['version']]
        for line in info[2:]:
            expected, path = line.split()
            assert hashlib.md5(archive.extractfile(path).read()).hexdigest() == expected
        kernel = archive.extractfile('recovery-update/xImage').read()
        assert sha(kernel) == manifest['kernel_sha256'] == (
            '39d54b09109db2b8fe9a975e3b24a0d105ef0b51a6708601af64aaa5186e11bc')
        assert archive.extractfile('recovery-update/rootfs.squashfs').read() == squash.read_bytes()
    print(directory.name, 'experimental/default package integrity passed')
print('PASS: AIC notifier build guards and all supplied packages')
