"""tools/mcu_park.py on a synthetic image of the stock file's size; its hash pin stands aside."""
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import types

import pytest

import mcu_park as m

HELPER = bytes(range(16))
SLEEP = {'q2_pwm_irq_return': m.MCU_BASE + m.HELPER_OFFSET + 4, 'q2_pwm_soft_irq': m.MCU_BASE + m.HELPER_OFFSET + 8}


def stock_image():
    image = bytearray(b'\xa5' * m.STOCK_SIZE)
    struct.pack_into('<II', image, m.HOOK_OFFSET, *m.HOOK_WORDS)
    struct.pack_into('<II', image, 0x1138, 0x8f5b0088, 0x409b7000)
    struct.pack_into('<II', image, 0x11e0, 0x3c02f400, 0x8c593f90)
    return bytes(image)


@pytest.fixture
def stock(monkeypatch):
    image = stock_image()
    monkeypatch.setattr(m, 'STOCK_SHA256', m.sha256(image))
    return image


def test_sha256():
    assert m.sha256(b'abc') == hashlib.sha256(b'abc').hexdigest()


def test_patch_image(stock):
    out = m.patch_image(stock, HELPER)
    assert len(out) == m.IMAGE_SIZE
    assert struct.unpack_from('<II', out, m.HOOK_OFFSET) == (0x09001b00, 0)
    assert out[m.HELPER_OFFSET:m.HELPER_OFFSET + 16] == HELPER and out[m.HELPER_OFFSET + 16] == 0
    assert struct.unpack_from('<IIII', out, m.PROTOCOL_OFFSET) == (0, 0, m.MAGIC, 1)
    assert out[:m.HOOK_OFFSET] == stock[:m.HOOK_OFFSET] and out[m.HOOK_OFFSET + 8:m.STOCK_SIZE] == stock[m.HOOK_OFFSET + 8:]
    assert out[m.STOCK_SIZE:m.HELPER_OFFSET] == bytes(m.HELPER_OFFSET - m.STOCK_SIZE)


def test_patch_image_sleep(stock):
    out = m.patch_image(stock, HELPER, sleep=True, symbols=SLEEP)
    assert len(out) == m.IMAGE_SIZE + 4
    assert struct.unpack_from('<I', out, m.PROTOCOL_OFFSET + 12)[0] == 2
    assert struct.unpack_from('<II', out, 0x1138) == (0x08000000 | (SLEEP['q2_pwm_irq_return'] >> 2 & 0x03ffffff), 0x8f5b0088)
    assert struct.unpack_from('<II', out, 0x11e0) == (0x08000000 | (SLEEP['q2_pwm_soft_irq'] >> 2 & 0x03ffffff), 0)


def test_patch_image_without_sleep_leaves_the_irq_hooks(stock):
    out = m.patch_image(stock, HELPER)
    assert out[0x1138:0x1140] == stock[0x1138:0x1140] and out[0x11e0:0x11e8] == stock[0x11e0:0x11e8]


def test_patch_image_compiles_its_own_helper(stock, monkeypatch):
    asked = []
    monkeypatch.setattr(m, 'compile_helper', lambda sleep=False: asked.append(sleep) or (HELPER, SLEEP))
    assert m.patch_image(stock) == m.patch_image(stock, HELPER)
    assert m.patch_image(stock, sleep=True) == m.patch_image(stock, HELPER, sleep=True, symbols=SLEEP)
    assert asked == [False, True]


def test_patch_image_sleep_helper_must_match_its_symbols(stock, monkeypatch):
    asked = []
    monkeypatch.setattr(m, 'compile_helper', lambda sleep=False: asked.append(sleep) or (HELPER, SLEEP))
    assert m.patch_image(stock, HELPER, sleep=True) == m.patch_image(stock, HELPER, sleep=True, symbols=SLEEP)
    assert asked == [True]
    with pytest.raises(ValueError, match='^Sleep helper does not match its linked symbols$'):
        m.patch_image(stock, b'\0' * 16, sleep=True)


def test_patch_image_needs_no_compile_when_given_symbols(stock, monkeypatch):
    monkeypatch.setattr(m, 'compile_helper', lambda sleep=False: pytest.fail('compiled'))
    m.patch_image(stock, HELPER)
    m.patch_image(stock, HELPER, sleep=True, symbols=SLEEP)


@pytest.mark.parametrize('image', [b'', b'\0' * m.STOCK_SIZE, stock_image()[:-1]])
def test_patch_image_refuses_other_images(stock, image):
    with pytest.raises(ValueError, match='^Unsupported MCU image: exact stock length/hash required$'):
        m.patch_image(image, HELPER)


def test_patch_image_refuses_a_wrong_hash():
    with pytest.raises(ValueError, match='^Unsupported MCU image'):
        m.patch_image(stock_image(), HELPER)


def test_patch_image_refuses_a_moved_hook(monkeypatch):
    image = bytearray(stock_image())
    image[m.HOOK_OFFSET + 4] ^= 1
    monkeypatch.setattr(m, 'STOCK_SHA256', m.sha256(bytes(image)))
    with pytest.raises(ValueError, match='^MCU idle hook instruction mismatch$'):
        m.patch_image(bytes(image), HELPER)


@pytest.mark.parametrize('helper', [b'\0' * 3, bytes(m.PROTOCOL_OFFSET - m.HELPER_OFFSET + 4)])
def test_patch_image_refuses_a_bad_helper(stock, helper):
    with pytest.raises(ValueError, match='^Invalid helper length$'):
        m.patch_image(stock, helper)


def test_patch_image_refuses_an_empty_helper(stock):
    with pytest.raises(ValueError, match='^Invalid helper length$'):
        m.patch_image(stock, b'', symbols={})


def test_patch_image_helper_may_fill_its_region(stock):
    helper = bytes(m.PROTOCOL_OFFSET - m.HELPER_OFFSET)
    assert m.patch_image(stock, helper)[m.HELPER_OFFSET:m.PROTOCOL_OFFSET] == helper


@pytest.mark.parametrize('offset', [0x1138, 0x113c, 0x11e0, 0x11e4])
def test_patch_image_refuses_a_moved_sleep_hook(offset, monkeypatch):
    image = bytearray(stock_image())
    image[offset] ^= 1
    monkeypatch.setattr(m, 'STOCK_SHA256', m.sha256(bytes(image)))
    assert m.patch_image(bytes(image), HELPER)  # only sleep reads them
    with pytest.raises(ValueError, match='^MCU sleep hook instruction mismatch$'):
        m.patch_image(bytes(image), HELPER, sleep=True, symbols=SLEEP)


@pytest.mark.parametrize('address', [m.MCU_BASE + m.HELPER_OFFSET - 4, m.MCU_BASE + m.HELPER_OFFSET + 16])
def test_patch_image_refuses_a_hook_outside_the_helper(stock, address):
    with pytest.raises(ValueError, match='^MCU sleep hook outside helper$'):
        m.patch_image(stock, HELPER, sleep=True, symbols={**SLEEP, 'q2_pwm_soft_irq': address})


def test_patch_image_hook_at_the_helper_start(stock):
    m.patch_image(stock, HELPER, sleep=True, symbols={**SLEEP, 'q2_pwm_soft_irq': m.MCU_BASE + m.HELPER_OFFSET})


class Tools:
    """compile_helper()'s four commands: they make the files, with these contents."""
    def __init__(self, monkeypatch, helper=b'\0' * 8, symbols=None):
        self.commands, self.helper = [], helper
        self.symbols = {'q2_pwm_idle': m.MCU_BASE + m.HELPER_OFFSET} if symbols is None else symbols
        monkeypatch.setattr(subprocess, 'run', self)

    def __call__(self, args, **kwargs):
        self.commands.append((args, kwargs))
        if args[0] == 'llvm-objcopy':
            with open(args[-1], 'wb') as f:
                f.write(self.helper)
        out = ''.join(f'{v:08x} T {k}\n' for k, v in self.symbols.items()) + '00000000 t other\n' if args[0] == 'llvm-nm' else ''
        return types.SimpleNamespace(stdout=out)


def test_compile_helper_commands(monkeypatch):
    tools = Tools(monkeypatch)
    data, symbols = m.compile_helper()
    assert data == b'\0' * 8 and symbols == {'q2_pwm_idle': 0xf4006c00}
    (cc, cck), (ld, ldk), (objcopy, ok), (nm, nmk) = tools.commands
    assert cc[:6] == ['clang', '--target=mipsel-none-elf', '-march=mips32r2', '-msoft-float', '-mno-abicalls', '-fno-pic']
    assert cc[6:9] == ['-c', str(m.SOURCE), '-o'] and cc[9].endswith('park.o')
    assert ld[:6] == ['ld.lld', '-m', 'elf32ltsmip', '-Ttext=0xf4006c00', '-e', 'q2_pwm_idle']
    assert ld[6] == cc[9] and ld[7] == '-o' and ld[8].endswith('park.elf')
    assert objcopy[:3] == ['llvm-objcopy', '-O', 'binary'] and objcopy[3:5] == ['--only-section=.text', ld[8]]
    assert objcopy[5].endswith('park.bin')
    assert nm == ['llvm-nm', '--defined-only', ld[8]]
    assert all(k == dict(check=True, capture_output=True) for k in (cck, ldk, ok))
    assert nmk == dict(check=True, capture_output=True, text=True)


def test_compile_helper_sleep_defines_it(monkeypatch):
    tools = Tools(monkeypatch, symbols={'q2_pwm_idle': 0xf4006c00, 'q2_pwm_sleep_check': 0x10, 'q2_pwm_sleep_end': 0x24})
    m.compile_helper(sleep=True)
    assert '-DQ2_MCU_SLEEP=1' in tools.commands[0][0]


@pytest.mark.parametrize('helper,symbols,message', [
    (b'', None, '^Helper exceeds reserved executable TCSM region$'),
    (b'\0' * 6, None, '^Helper exceeds reserved executable TCSM region$'),
    (bytes(m.PROTOCOL_OFFSET - m.HELPER_OFFSET + 4), None, '^Helper exceeds reserved executable TCSM region$'),
    (b'\0' * 8, {'q2_pwm_idle': 0xf4006c04}, '^Unexpected helper link address$'),
    (b'\0' * 8, {}, '^Unexpected helper link address$'),
])
def test_compile_helper_refuses(monkeypatch, helper, symbols, message):
    Tools(monkeypatch, helper, symbols)
    with pytest.raises(ValueError, match=message):
        m.compile_helper()


def test_compile_helper_fills_the_region_exactly(monkeypatch):
    Tools(monkeypatch, bytes(m.PROTOCOL_OFFSET - m.HELPER_OFFSET))
    m.compile_helper()


def test_compile_helper_keeps_only_its_own_symbols(monkeypatch):
    Tools(monkeypatch, symbols={'q2_pwm_idle': 0xf4006c00, 'q2_pwm_park': 0xf4006c10, 'other': 1})
    assert set(m.compile_helper()[1]) == {'q2_pwm_idle', 'q2_pwm_park'}


def test_compile_helper_sleep_window(monkeypatch):
    Tools(monkeypatch, symbols={'q2_pwm_idle': 0xf4006c00, 'q2_pwm_sleep_check': 0x10, 'q2_pwm_sleep_end': 0x20})
    assert m.compile_helper()  # only sleep reads it
    with pytest.raises(ValueError, match='^Sleep IRQ-recheck window changed$'):
        m.compile_helper(sleep=True)


def test_compile_helper_names_missing_tools(monkeypatch):
    monkeypatch.setattr(shutil, 'which', lambda name: None if name in ('ld.lld', 'llvm-nm') else '/usr/bin/x')
    with pytest.raises(RuntimeError, match='^Missing assembler tools: ld.lld, llvm-nm$'):
        m.compile_helper()


def test_make_manifest(stock):
    patched = m.patch_image(stock, HELPER)
    symbols = {'q2_pwm_idle': 0xf4006c00}
    out = m.make_manifest(stock, patched, HELPER, symbols)
    assert out['sleep_policy'] == 'poll'
    assert out['stock'] == {'sha256': m.sha256(stock), 'size': m.STOCK_SIZE}
    assert out['patched'] == {'sha256': m.sha256(patched), 'size': m.IMAGE_SIZE}
    assert out['assembly'] == {'path': str(m.SOURCE), 'sha256': m.sha256(m.SOURCE.read_bytes())}
    assert out['helper'] == {'offset': m.HELPER_OFFSET, 'size': 16, 'sha256': m.sha256(HELPER), 'symbols': symbols}
    assert out['hook'] == {'offset': m.HOOK_OFFSET, 'stock_words': list(m.HOOK_WORDS), 'patched_words': [0x09001b00, 0],
                           'resume_offset': m.RETURN_OFFSET}
    protocol = out['protocol']
    assert protocol['mcu_req'] == 0xf4007f00 and protocol['mcu_ack'] == 0xf4007f04
    assert protocol['linux_req'] == 0xb3429f00 and protocol['linux_ack'] == 0xb3429f04
    assert protocol['magic'] == m.MAGIC and protocol['version'] == 1
    assert (protocol['request_run'], protocol['request_park'], protocol['ack_run'], protocol['ack_park']) == (0, 1, 0, 1)
    assert 'mcu_wake' not in protocol and protocol['host_requirement'].startswith('Initialize REQ/ACK while reset')
    assert [(r['start'], r['end_exclusive']) for r in out['immutable_ranges']] == [
        (0, 0x3cc8), (m.HELPER_OFFSET, m.HELPER_OFFSET + 16), (m.PROTOCOL_OFFSET + 8, m.PROTOCOL_OFFSET + 16)]
    assert out['immutable_ranges'][1]['sha256'] == m.sha256(HELPER)
    assert out['preserved_stock_ranges'] == [[0, m.HOOK_OFFSET], [m.HOOK_OFFSET + 8, m.STOCK_SIZE]]
    assert out['runtime_layout'] == {'stock_bss': [0x3f88, 0x4ba8], 'initial_stack_offset': 0x6ba8, 'helper_stack_bytes': 32,
                                     'ring_writer_offset': 0x3ce8, 'ring_reader_offset': 0x3cec, 'mailbox_flag_offset': 0x3f8c,
                                     'irq_mask_address': 0x10001038, 'pwm_irq_mask_bit': 6}
    assert out['limits'] == ['No physical clock or current measurements',
                             'Requires paired Linux policy; unsafe to gate based only on a sampled idle flag',
                             'Does not stop pre-existing PWM/DMA left active by reset or unsupported firmware',
                             'No actual-device validation is implied by building this artifact']
    assert out['purpose'] == 'Cooperative PWM park with optional IRQ-safe MCU WAIT; no clock writes'


def test_make_manifest_sleep(stock):
    patched = m.patch_image(stock, HELPER, sleep=True, symbols=SLEEP)
    out = m.make_manifest(stock, patched, HELPER, SLEEP, sleep=True)
    assert out['sleep_policy'] == 'irq-epc-recheck-v1'
    protocol = out['protocol']
    assert protocol['version'] == 2 and protocol['mcu_wake'] == 0xf4007f10 and protocol['linux_wake'] == 0xb3429f10
    assert protocol['host_sftint'] == 0xb00000bc and protocol['sleep_status'] == 0xb3421030 and protocol['sleep_bit'] == 31
    assert protocol['host_requirement'].startswith('Initialize REQ/ACK/WAKE under reset')
    assert out['preserved_stock_ranges'] == [[0, m.HOOK_OFFSET], [m.HOOK_OFFSET + 8, 0x1138], [0x1140, 0x11e0], [0x11e8, m.STOCK_SIZE]]


def test_main(stock, tmp_path, monkeypatch, capsys):
    (tmp_path/'stock').write_bytes(stock)
    monkeypatch.setattr(m, 'compile_helper', lambda sleep=False: (HELPER, {'q2_pwm_idle': 0xf4006c00}))
    monkeypatch.setattr(sys, 'argv', ['mcu_park.py', str(tmp_path/'stock'), str(tmp_path/'out.bin')])
    m.main()
    patched = m.patch_image(stock, HELPER)
    assert (tmp_path/'out.bin').read_bytes() == patched
    manifest = (tmp_path/'out.json').read_text()
    assert manifest.endswith('}\n') and json.loads(manifest) == m.make_manifest(stock, patched, HELPER, {'q2_pwm_idle': 0xf4006c00})
    assert manifest == json.dumps(json.loads(manifest), indent=2) + '\n'
    assert capsys.readouterr().out == json.dumps({'output': str(tmp_path/'out.bin'), 'manifest': str(tmp_path/'out.json'),
                                                  'sha256': m.sha256(patched), 'bytes': len(patched), 'helper_bytes': 16}, sort_keys=True) + '\n'


def test_main_sleep_and_manifest_path(stock, tmp_path, monkeypatch):
    (tmp_path/'stock').write_bytes(stock)
    asked = []
    monkeypatch.setattr(m, 'compile_helper', lambda sleep=False: asked.append(sleep) or (HELPER, SLEEP))
    monkeypatch.setattr(sys, 'argv', ['mcu_park.py', str(tmp_path/'stock'), str(tmp_path/'out.bin'),
                                      '--sleep', '--manifest', str(tmp_path/'m')])
    m.main()
    assert asked == [True] and json.loads((tmp_path/'m').read_text())['sleep_policy'] == 'irq-epc-recheck-v1'
    assert (tmp_path/'out.bin').read_bytes() == m.patch_image(stock, HELPER, sleep=True, symbols=SLEEP)
    assert not (tmp_path/'out.json').exists()


def test_real_helper_assembles():
    if not shutil.which('clang') or not shutil.which('ld.lld'):
        pytest.skip('no LLVM')
    for sleep in (False, True):
        data, symbols = m.compile_helper(sleep=sleep)
        assert symbols['q2_pwm_idle'] == 0xf4006c00 and len(data) % 4 == 0
