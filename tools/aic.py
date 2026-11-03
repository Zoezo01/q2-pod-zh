#!/usr/bin/env python3
"""Opt-in stopped-notifier event wait for one audited Q2 soc_aic.ko image."""
import argparse
import hashlib
from pathlib import Path
import struct
import subprocess
import tempfile

from module_elf import Elf, require

STOCK_SHA256 = '97b141982aa39a0551aa0505cbf041ab4001d4a136bc3b5637ca11a1292d591c'
MODULE_PATH = 'module_driver/soc_aic.ko'
PATCH_POLICY = 'stopped-event-wait-v1'
ROOT = Path(__file__).resolve().parent
CFLAGS = ['--target=mipsel-linux-gnu', '-march=mips32r5', '-mabi=32',
          '-msoft-float', '-mno-abicalls', '-fno-pic', '-mlong-calls', '-G0',
          '-ffreestanding', '-fno-stack-protector', '-fno-builtin', '-Os',
          '-Wall', '-Wextra', '-Werror']


def undefined(elf, name):
    strings = elf.by_name['.strtab']['data']
    n, index = len(strings), len(elf.symbols)
    strings.extend(name.encode() + b'\0')
    elf.by_name['.symtab']['data'].extend(struct.pack('<IIIBBH', n, 0, 0, 0x10, 0, 0))
    elf.symbols.append(dict(name=name, value=0, size=0, info=0x10, other=0, section=0))
    return index


def validate_stock(elf):
    text = elf.by_name['.text']['data']
    require(len(text) == 0x2330 and elf.by_name['.bss']['size'] == 0x870,
            'Audio module layout changed')
    expected = {
        # Active timing/notification remains byte-for-byte stock.
        0x1810: 0x0240f809, 0x1814: 0, 0x1818: 0x0260f809, 0x181c: 0x8e04000c,
        0x182c: 0x8e020030,
        0x183c: 0x24052710, 0x1840: 0x0240f809, 0x1844: 0x24042710,
        0x1848: 0x8e020028, 0x185c: 0xae02002c,
        0x199c: 0x26440034, 0x19b8: 0x0040f809,
        0x1c58: 0xae000028, 0x1c5c: 0xae00002c, 0x1c74: 0xae000030,
        0x1ff0: 0xae020028, 0x1ff4: 0x8e02002c, 0x1ff8: 0x10400041,
        0x2104: 0x27a40020, 0x212c: 0x24060002,
        0x21dc: 0xae020030, 0x21e0: 0x00001025, 0x21e4: 0x8fbf0014,
        0x2208: 0xae000030,
    }
    for offset, word in expected.items():
        require(struct.unpack_from('<I', text, offset)[0] == word,
                'Audio instruction changed at %#x' % offset)
    rels = {(off, elf.symbols[info >> 8]['name'], info & 255)
            for off, info in struct.iter_unpack('<II', elf.by_name['.rel.text']['data'])}
    for item in [(0x17fc, 'usleep_range', 5), (0x1804, 'usleep_range', 6),
                 (0x1800, 'snd_pcm_period_elapsed', 5), (0x180c, 'snd_pcm_period_elapsed', 6),
                 (0x19b0, '__init_waitqueue_head', 5), (0x19b4, '__init_waitqueue_head', 6),
                 (0x2114, 'prepare_to_wait_event', 5), (0x2120, 'prepare_to_wait_event', 6),
                 (0x2118, 'schedule_timeout', 5), (0x2124, 'schedule_timeout', 6)]:
        require(item in rels, 'Audio relocation changed: %r' % (item,))
    require(not any(lo <= off < hi for off, _, _ in rels
                    for lo, hi in [(0x183c, 0x1848), (0x1ff0, 0x1ff8), (0x21dc, 0x21e4)]),
            'Unexpected relocation at audio hook')


def patch_module(data):
    require(hashlib.sha256(data).hexdigest() == STOCK_SHA256,
            'Unsupported or already patched audio module')
    elf = Elf(data)
    validate_stock(elf)
    text = elf.by_name['.text']['data']
    rels = list(struct.iter_unpack('<II', elf.by_name['.rel.text']['data']))
    section = elf.symbol_index('.text')
    wait = undefined(elf, 'q2_aic_wait_stopped')
    wake = undefined(elf, 'q2_aic_wake_notifier')

    def hook(site, function, after, delay, replay=0):
        # The entry and return J relocations stay in this one .text section.
        # The helper call uses a full HI16/LO16 address + JALR, never an
        # external R_MIPS_26 (module/kernel may occupy different 256 MiB regions).
        start = len(text)
        words = [0x3c190000, 0x27390000, 0x0320f809, 0x02002025,
                 replay, 0x08000000 | (after >> 2), 0]
        text.extend(struct.pack('<7I', *words))
        rels.extend([(start, function << 8 | 5), (start + 4, function << 8 | 6),
                     (start + 20, section << 8 | 4), (site, section << 8 | 4)])
        struct.pack_into('<II', text, site, 0x08000000 | (start >> 2), delay)
        elf.add_function('q2_aic_site_%x' % site, start, 28)

    hook(0x183c, wait, 0x1848, 0)
    struct.pack_into('<I', text, 0x1844, 0)
    # Keep each producer's original store in the entry jump's delay slot.
    # Every value live at each continuation is either callee-saved or replayed.
    hook(0x21dc, wake, 0x21e4, 0xae020030, 0x00001025)
    hook(0x1ff0, wake, 0x1ff8, 0xae020028, 0x8e02002c)
    elf.by_name['.rel.text']['data'] = bytearray(
        b''.join(struct.pack('<II', *item) for item in rels))
    with tempfile.TemporaryDirectory(prefix='q2-aic-') as directory:
        p = Path(directory)
        (p / 'stock.ko').write_bytes(elf.pack())
        subprocess.run(['clang', *CFLAGS, '-c', str(ROOT / 'aic_idle_wait.c'),
                        '-o', str(p / 'helper.o')], check=True)
        subprocess.run(['ld.lld', '-r', '--build-id=sha1', str(p / 'stock.ko'),
                        str(p / 'helper.o'), '-o', str(p / 'linked.ko')], check=True)
        result = Elf((p / 'linked.ko').read_bytes())
    result.by_name['.modinfo']['data'].extend(
        ('q2_aic_notifier=' + PATCH_POLICY).encode() + b'\0')
    note = result.by_name['.note.gnu.build-id']['data']
    require(len(note) == 36 and note[12:16] == b'GNU\0', 'Unexpected build ID')
    note[16:36] = bytes(20)
    note[16:36] = hashlib.sha256(result.pack()).digest()[:20]
    require(result.header[7] == elf.header[7], 'Audio ELF ABI flags changed')
    require(result.by_name['.MIPS.abiflags']['data'] == elf.by_name['.MIPS.abiflags']['data'],
            'Audio MIPS ABI changed')
    # Kernel module allocation can split init/core; reject every non-local J/JAL.
    for rel in result.sections:
        if rel['type'] == 9 and result.sections[rel['info']]['flags'] & 2:
            for _, info in struct.iter_unpack('<II', rel['data']):
                if info & 255 == 4:
                    require(result.symbols[info >> 8]['section'] == rel['info'],
                            'Non-local R_MIPS_26 in audio module')
    return result.pack()


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stock', type=Path)
    ap.add_argument('out', type=Path)
    args = ap.parse_args()
    args.out.write_bytes(patch_module(args.stock.read_bytes()))
