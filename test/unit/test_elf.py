"""The ELF surgery in tools/, on a small synthetic relocatable module: no stock .ko involved."""
import hashlib
import struct

import pytest

import aic
import module_elf
import pwm
import rotator

SECTIONS = ('', '.text', '.rel.text', '.init.text', '.rel.init.text', '.bss', '.symtab', '.strtab', '.shstrtab', '.modinfo',
            '.gnu.linkonce.this_module', '.rel.gnu.linkonce.this_module', '.note.gnu.build-id')
TYPES = {'': 0, '.bss': 8, '.symtab': 2, '.strtab': 3, '.shstrtab': 3, '.rel.text': 9, '.rel.init.text': 9, '.rel.gnu.linkonce.this_module': 9}
INDEX = {name: i for i, name in enumerate(SECTIONS)}
# name, value, size, info, section
SYMBOLS = [('', 0, 0, 0, 0), ('.text', 0, 0, 3, INDEX['.text']), ('.init.text', 0, 0, 3, INDEX['.init.text']),
           ('fn', 0x10, 8, 0x12, INDEX['.text']), ('probe', 0x4, 8, 0x12, INDEX['.init.text']),
           ('clk_enable', 0, 0, 0x10, 0), ('clk_disable', 0, 0, 0x10, 0), ('cleanup_module', 0, 0, 0x12, INDEX['.text']),
           ('init_module', 0, 0, 0x12, INDEX['.text']), ('absolute', 0xc0001000, 0, 0x80, 0xfff1)]
SYMBOL = {s[0]: i for i, s in enumerate(SYMBOLS) if s[0]}


def elf(text=None, rel_text=(), rel_init=(), this_module=()):
    """A module with SYMBOLS: .text and .init.text of 0x20 and 0x10 words of `addiu` padding."""
    strtab, symtab = bytearray(b'\0'), bytearray()
    for name, value, size, info, section in SYMBOLS:
        n = len(strtab) if name else 0
        if name:
            strtab += name.encode() + b'\0'
        symtab += struct.pack('<IIIBBH', n, value, size, info, 0, section)
    padding = struct.pack('<I', 0x24020001) * 0x20
    body = {'.text': text or padding, '.init.text': padding[:0x40],
            '.rel.text': b''.join(struct.pack('<II', *r) for r in rel_text),
            '.rel.init.text': b''.join(struct.pack('<II', *r) for r in rel_init),
            '.symtab': bytes(symtab), '.strtab': bytes(strtab),
            '.modinfo': b'depends=utils\0', '.gnu.linkonce.this_module': bytes(0x20),
            '.rel.gnu.linkonce.this_module': b''.join(struct.pack('<II', *r) for r in this_module),
            '.note.gnu.build-id': struct.pack('<III', 4, 20, 3) + b'GNU\0' + bytes(20)}
    names, offsets = bytearray(), {}
    for name in SECTIONS:
        offsets[name] = len(names)
        names += name.encode() + b'\0'
    body['.shstrtab'] = bytes(names)
    data = bytearray(52)
    table = []
    for name in SECTIONS:
        chunk = body.get(name, b'')
        data += bytes(-len(data) % 4)
        kind = TYPES.get(name, 1)
        size = 0x80 if name == '.bss' else len(chunk)
        link, info = 0, 0
        if kind == 9:
            link, info = INDEX['.symtab'], INDEX[name[4:]]
        table.append((offsets[name], kind, 2 if name == '.text' else 0, 0, len(data) if name else 0, size, link, info, 4 if name else 0, 0))
        if kind != 8:
            data += chunk
    data += bytes(-len(data) % 4)
    shoff = len(data)
    for row in table:
        data += struct.pack('<10I', *row)
    struct.pack_into('<16sHHIIIIIHHHHHH', data, 0, b'\x7fELF\x01\x01\x01' + bytes(9), 1, 8, 1, 0, 0, shoff, 0x70001000,
                     52, 0, 0, 40, len(SECTIONS), INDEX['.shstrtab'])
    return bytes(data)


@pytest.fixture(params=[module_elf, rotator], ids=['module_elf', 'rotator'])
def lib(request):
    return request.param


def rels(section):
    return list(struct.iter_unpack('<II', section['data']))


def test_require(lib):
    assert lib.require(True, 'x') is None
    with pytest.raises(ValueError, match='^x$'):
        lib.require(False, 'x')


def test_elf_reads_sections_and_symbols(lib):
    e = lib.Elf(elf())
    assert [s['name'] for s in e.sections] == list(SECTIONS)
    assert e.by_name['.text']['data'][:4] == struct.pack('<I', 0x24020001) and len(e.by_name['.text']['data']) == 0x80
    assert e.by_name['.bss']['data'] == b'' and e.by_name['.bss']['size'] == 0x80
    assert e.symbols[3] == dict(name='fn', value=0x10, size=8, info=0x12, other=0, section=1)
    assert e.symbols[SYMBOL['absolute']] == dict(name='absolute', value=0xc0001000, size=0, info=0x80, other=0, section=0xfff1)
    assert [s['name'] for s in e.symbols] == [s[0] for s in SYMBOLS]
    assert e.header[12] == len(SECTIONS)


@pytest.mark.parametrize('offset,value', [(0, b'\x7fELG'), (4, b'\x02'), (5, b'\x02'), (6, b'\x02'), (16, b'\x02\0'),
                                          (18, b'\x09\0')])
def test_elf_refuses_another_file(lib, offset, value):
    data = bytearray(elf())
    data[offset:offset + len(value)] = value
    with pytest.raises(ValueError, match='^Expected MIPS ELF32 relocatable$'):
        lib.Elf(bytes(data))


@pytest.mark.parametrize('offset,value', [(28, 1), (40, 40), (46, 41)])
def test_elf_refuses_unexpected_headers(lib, offset, value):
    data = bytearray(elf())
    if offset == 28:
        struct.pack_into('<I', data, offset, value)
    else:
        struct.pack_into('<H', data, offset, value)
    with pytest.raises(ValueError, match='^Unexpected ELF headers$'):
        lib.Elf(bytes(data))


def test_symbol_index(lib):
    e = lib.Elf(elf())
    assert e.symbol_index('.text') == SYMBOL['.text'] and e.symbol_index('.init.text') == SYMBOL['.init.text']
    assert e.symbol_index('fn') == SYMBOL['fn'] and e.symbol_index('clk_disable') == SYMBOL['clk_disable']
    with pytest.raises(StopIteration):
        e.symbol_index('absent')


def test_symbol_index_of_a_section_wants_its_section_symbol(lib):
    data = elf()
    e = lib.Elf(data)
    e.symbols.insert(1, dict(name='decoy', value=0, size=0, info=0x12, other=0, section=1))  # right section, a function
    e.symbols.insert(2, dict(name='decoy', value=0, size=0, info=3, other=0, section=3))     # a section symbol, another's
    assert e.symbol_index('.text') == SYMBOL['.text'] + 2


def test_pack_round_trip(lib):
    data = elf()
    e = lib.Elf(data)
    again = lib.Elf(e.pack())
    assert [(s['name'], s['type'], s['flags'], s['size'], s['link'], s['info'], bytes(s['data'])) for s in again.sections] == \
           [(s['name'], s['type'], s['flags'], s['size'], s['link'], s['info'], bytes(s['data'])) for s in e.sections]
    assert again.symbols == e.symbols and again.header[7] == 0x70001000


def test_pack_aligns_and_grows(lib):
    e = lib.Elf(elf())
    e.by_name['.text']['align'] = 16
    e.by_name['.text']['data'].extend(b'\x11\x22\x33')
    e.by_name['.bss']['align'] = 0  # no alignment asked for
    data = e.pack()
    assert e.by_name['.text']['offset'] % 16 == 0 and e.by_name['.text']['size'] == 0x83
    assert data[e.by_name['.text']['offset'] + 0x80:e.by_name['.text']['offset'] + 0x83] == b'\x11\x22\x33'
    assert e.header[6] % 4 == 0 and e.header[6] + len(SECTIONS) * 40 == len(data)
    assert data[:4] == b'\x7fELF' and struct.unpack_from('<I', data, 32)[0] == e.header[6]
    for s in e.sections[1:]:
        if s['type'] != 8:
            assert data[s['offset']:s['offset'] + s['size']] == bytes(s['data'])


def test_pack_first_section_follows_the_header(lib):
    e = lib.Elf(elf())
    e.pack()
    assert e.sections[1]['offset'] == 52


def test_pack_alignment_one_adds_nothing(lib):
    e = lib.Elf(elf())
    e.by_name['.text']['data'].append(1)  # odd
    e.by_name['.rel.text']['align'] = 1
    e.pack()
    assert e.by_name['.rel.text']['offset'] == e.by_name['.text']['offset'] + 0x81


def test_pack_section_table_is_word_aligned(lib):
    e = lib.Elf(elf())
    e.sections[-1]['data'].append(1)
    data = e.pack()
    assert e.header[6] % 4 == 0 and e.header[6] + len(SECTIONS) * 40 == len(data)


def test_pack_nobits_takes_no_room(lib):
    e = lib.Elf(elf())
    e.by_name['.bss']['size'] = 0x1000
    data = e.pack()
    assert e.by_name['.bss']['size'] == 0x1000 and len(data) < 0x1000


def test_pack_leaves_the_null_section_at_zero(lib):
    e = lib.Elf(elf())
    e.pack()
    assert e.sections[0]['offset'] == 0


def test_add_function_module_elf():
    e = module_elf.Elf(elf())
    strings = len(e.by_name['.strtab']['data'])
    count = len(e.symbols)
    e.add_function('new', 0x20, 12)
    assert e.by_name['.strtab']['data'][strings:] == b'new\0'
    assert struct.unpack_from('<IIIBBH', e.by_name['.symtab']['data'], count * 16) == (strings, 0x20, 12, 0x12, 0, 1)
    assert e.symbols[count] == dict(name='new', value=0x20, size=12, info=0x12, other=0, section=1)
    assert module_elf.Elf(e.pack()).symbols[count]['name'] == 'new'


def test_add_function_rotator():
    e = rotator.Elf(elf())
    strings = len(e.by_name['.strtab']['data'])
    count = len(SYMBOLS)
    e.add_function('new', 0x20, 12)
    assert e.by_name['.strtab']['data'][strings:] == b'new\0'
    assert struct.unpack_from('<IIIBBH', e.by_name['.symtab']['data'], count * 16) == (strings, 0x20, 12, 0x12, 0, 3)


@pytest.fixture
def code():
    return rotator.Code(rotator.Elf(elf()), 0x100)


def test_code_emit_and_pc(code):
    assert code.pc == 0x100
    code.emit(7)
    assert code.words == [7] and code.pc == 0x104


def test_code_imm_fields(code):
    code.imm(9, 2, 3, -1)
    code.imm(35, 31, 29, 0x12345)
    assert code.words == [9 << 26 | 3 << 21 | 2 << 16 | 0xffff, 35 << 26 | 29 << 21 | 31 << 16 | 0x2345]


def test_code_address_is_a_hi_lo_pair(code):
    code.address(4, 'clk_enable')
    assert code.words == [15 << 26 | 4 << 16, 9 << 26 | 4 << 21 | 4 << 16]
    assert code.relocations == [(0x100, SYMBOL['clk_enable'] << 8 | 5), (0x104, SYMBOL['clk_enable'] << 8 | 6)]


def test_code_call_clock(code):
    code.call_clock('clk_disable')
    assert code.words == [15 << 26 | 2 << 16, 9 << 26 | 2 << 21 | 2 << 16, 0x0040f809, 35 << 26 | 18 << 21 | 4 << 16]
    assert [r[1] for r in code.relocations] == [SYMBOL['clk_disable'] << 8 | 5, SYMBOL['clk_disable'] << 8 | 6]


def test_code_jump(code):
    code.emit(0)
    code.jump(0x408)
    assert code.words[1:] == [0x08000000 | 0x408 >> 2, 0]
    assert code.relocations == [(0x104, SYMBOL['.text'] << 8 | 4)]


def test_code_branch_forward_and_back(code):
    code.label('top')
    code.branch(4, 2, 3, 'done')
    code.emit(0x8e510040)  # lw: past 2^31
    code.branch(5, 6, 7, 'top')
    code.label('done')
    data = code.finish()
    words = struct.unpack(f'<{len(data) // 4}I', data)
    assert words == (4 << 26 | 2 << 21 | 3 << 16 | 4, 0, 0x8e510040, 5 << 26 | 6 << 21 | 7 << 16 | (-4 & 0xffff), 0)


@pytest.mark.parametrize('delta,ok', [(32767, True), (32768, False), (-32768, True), (-32769, False)])
def test_code_branch_range(code, delta, ok):
    code.branch(4, 0, 0, 'far')
    code.labels['far'] = 0x104 + delta * 4
    if ok:
        code.finish()
        assert code.words[0] & 0xffff == delta & 0xffff
    else:
        with pytest.raises(ValueError, match='^Branch out of range$'):
            code.finish()


def test_aic_undefined():
    e = module_elf.Elf(elf())
    strings = len(e.by_name['.strtab']['data'])
    count = len(e.symbols)
    assert aic.undefined(e, 'helper') == count
    assert e.by_name['.strtab']['data'][strings:] == b'helper\0'
    assert struct.unpack_from('<IIIBBH', e.by_name['.symtab']['data'], count * 16) == (strings, 0, 0, 0x10, 0, 0)
    assert e.symbols[count] == dict(name='helper', value=0, size=0, info=0x10, other=0, section=0)
    assert e.symbol_index('helper') == count


def test_pwm_symbol():
    e = rotator.Elf(elf())
    count = len(e.symbols)
    strings = len(e.by_name['.strtab']['data'])
    assert pwm.symbol(e, 'a') == count
    assert pwm.symbol(e, 'b', 1, 2, 3, 0x12) == count + 1
    assert e.by_name['.strtab']['data'][strings:] == b'a\0b\0'
    assert struct.unpack_from('<IIIBBH', e.by_name['.symtab']['data'], count * 16) == (strings, 0, 0, 0x10, 0, 0)
    assert struct.unpack_from('<IIIBBH', e.by_name['.symtab']['data'], (count + 1) * 16) == (strings + 2, 1, 2, 0x12, 0, 3)
    assert e.symbols[count + 1] == dict(name='b', value=1, size=2, info=0x12, other=0, section=3)


def test_pwm_alias():
    e = rotator.Elf(elf())
    index = pwm.alias(e, 'fn', 'q2_fn')
    assert e.symbols[index] == dict(name='q2_fn', value=0x10, size=8, info=0x12, other=0, section=1)


def test_pwm_wrap_in_text():
    prologue = struct.pack('<II', 0x3c040000, 0x24840000)
    text = bytearray(struct.pack('<I', 0x24020001) * 0x20)
    text[0x10:0x18] = prologue
    hi, lo = SYMBOL['clk_enable'] << 8 | 5, SYMBOL['clk_enable'] << 8 | 6
    e = rotator.Elf(elf(bytes(text), rel_text=[(0x10, hi), (0x14, lo), (0x40, hi), (0xc, lo)]))
    pwm.wrap(e, 'fn', 'q2_fn')
    data = e.by_name['.text']['data']
    target = e.symbol_index('q2_fn')
    assert e.symbols[target]['section'] == 0 and e.symbols[target]['info'] == 0x10
    assert struct.unpack_from('<II', data, 0x10) == (0x08000000, 0)  # entry: a J to the helper, relocated
    tramp = 0x80
    assert bytes(data[tramp:tramp + 8]) == prologue
    assert struct.unpack_from('<II', data, tramp + 8) == (0x08000000 | 0x18 >> 2, 0)
    assert rels(e.by_name['.rel.text']) == [(tramp, hi), (tramp + 4, lo), (0x40, hi), (0xc, lo),
                                            (0x10, target << 8 | 4), (tramp + 8, SYMBOL['.text'] << 8 | 4)]
    stock = next(s for s in e.symbols if s['name'] == 'q2_stock_fn')
    assert stock == dict(name='q2_stock_fn', value=tramp, size=16, info=0x12, other=0, section=1)


def test_pwm_wrap_in_init_uses_a_full_address():
    kept = (0x30, SYMBOL['clk_enable'] << 8 | 4)
    e = rotator.Elf(elf(rel_init=[kept]))
    pwm.wrap(e, 'probe', 'q2_probe')
    data = e.by_name['.init.text']['data']
    tramp, veneer = 0x40, 0x50
    target = e.symbol_index('q2_probe')
    section = SYMBOL['.init.text']
    assert struct.unpack_from('<II', data, 0x4) == (0x08000000 | veneer >> 2, 0)
    assert struct.unpack_from('<IIII', data, veneer) == (0x3c190000, 0x27390000, 0x03200008, 0)
    assert rels(e.by_name['.rel.init.text']) == [kept, (0x4, section << 8 | 4), (tramp + 8, section << 8 | 4),
                                                 (veneer, target << 8 | 5), (veneer + 4, target << 8 | 6)]
    assert next(s for s in e.symbols if s['name'] == 'q2_stock_probe')['section'] == INDEX['.init.text']


@pytest.mark.parametrize('opcode', [1, 2, 3, 4, 5, 6, 7, 20, 21, 22, 23])
@pytest.mark.parametrize('slot', [0, 4])
def test_pwm_wrap_refuses_control_flow(opcode, slot):
    text = bytearray(struct.pack('<I', 0x24020001) * 0x20)
    struct.pack_into('<I', text, 0x10 + slot, opcode << 26)
    e = rotator.Elf(elf(bytes(text)))
    with pytest.raises(ValueError, match='^Branch in prologue$'):
        pwm.wrap(e, 'fn', 'q2_fn')


@pytest.mark.parametrize('opcode', [0, 8, 9, 15, 19, 24, 35, 43])
def test_pwm_wrap_takes_plain_instructions(opcode):
    text = bytearray(struct.pack('<I', 0x24020001) * 0x20)
    struct.pack_into('<I', text, 0x10, opcode << 26)
    pwm.wrap(rotator.Elf(elf(bytes(text))), 'fn', 'q2_fn')


def test_pwm_wrap_moves_relocations_only_inside_the_prologue():
    hi = SYMBOL['clk_enable'] << 8 | 5
    e = rotator.Elf(elf(rel_text=[(0xc, hi), (0x10, hi), (0x14, hi), (0x18, hi)]))
    pwm.wrap(e, 'fn', 'q2_fn')
    assert [r[0] for r in rels(e.by_name['.rel.text'])][:4] == [0xc, 0x80, 0x84, 0x18]


def test_pwm_rebind():
    old, other = SYMBOL['clk_enable'], SYMBOL['clk_disable']
    e = rotator.Elf(elf(rel_text=[(0x10, old << 8 | 4), (0x14, old << 8 | 5), (0x18, other << 8 | 4), (0x20, old << 8 | 4), (0xc, old << 8 | 4)]))
    pwm.rebind(e, '.text', 0x10, 0x20, 'clk_enable', 'q2_enable')
    new = e.symbol_index('q2_enable')
    assert rels(e.by_name['.rel.text']) == [(0x10, new << 8 | 4), (0x14, new << 8 | 5), (0x18, other << 8 | 4),
                                            (0x20, old << 8 | 4), (0xc, old << 8 | 4)]


def test_pwm_rebind_one_call_is_enough():
    e = rotator.Elf(elf(rel_text=[(0x10, SYMBOL['clk_enable'] << 8 | 4)]))
    pwm.rebind(e, '.text', 0x0, 0x20, 'clk_enable', 'new')
    assert rels(e.by_name['.rel.text']) == [(0x10, e.symbol_index('new') << 8 | 4)]


def test_pwm_rebind_needs_a_call():
    e = rotator.Elf(elf(rel_text=[(0x10, SYMBOL['clk_disable'] << 8 | 4)]))
    with pytest.raises(ValueError, match='^No call relocations for clk_enable$'):
        pwm.rebind(e, '.text', 0x0, 0x20, 'clk_enable', 'new')


def test_pwm_permanent_clears_the_exit_pointer():
    cleanup, init = SYMBOL['cleanup_module'], SYMBOL['init_module']
    e = rotator.Elf(elf(this_module=[(0x8, init << 8 | 2), (0x10, cleanup << 8 | 2), (0xc, init << 8 | 2)]))
    struct.pack_into('<I', e.by_name['.gnu.linkonce.this_module']['data'], 0x10, 0xdead)
    struct.pack_into('<I', e.by_name['.gnu.linkonce.this_module']['data'], 0x8, 0xbeef)
    pwm.permanent(e)
    data = e.by_name['.gnu.linkonce.this_module']['data']
    assert struct.unpack_from('<I', data, 0x10)[0] == 0 and struct.unpack_from('<I', data, 0x8)[0] == 0xbeef
    assert rels(e.by_name['.rel.gnu.linkonce.this_module']) == [(0x8, init << 8 | 2), (0xc, init << 8 | 2)]


def test_pwm_permanent_wants_an_absolute_exit_relocation():
    e = rotator.Elf(elf(this_module=[(0x10, SYMBOL['cleanup_module'] << 8 | 3)]))
    with pytest.raises(ValueError, match='^Unexpected module exit relocation$'):
        pwm.permanent(e)


@pytest.mark.parametrize('rows', [[], [(0x10, SYMBOL['cleanup_module'] << 8 | 2), (0x14, SYMBOL['cleanup_module'] << 8 | 2)]])
def test_pwm_permanent_wants_exactly_one(rows):
    with pytest.raises(ValueError, match='^Expected one module exit pointer$'):
        pwm.permanent(rotator.Elf(elf(this_module=rows)))


def test_pwm_expected_header():
    image = bytes(i & 0xff for i in range(0x8000))
    lines = pwm.expected_header(image, 4).splitlines()
    assert lines[0] == 'struct q2_expected { u32 offset,length; const unsigned char *bytes; };'
    assert lines[1].startswith('static const unsigned char q2_expected_0[]={0,1,2,') and lines[1].endswith(',198,199};')
    assert lines[1].count(',') == 0x3cc8 - 1
    assert lines[2] == 'static const unsigned char q2_expected_1[]={0,1,2,3};'
    assert lines[3] == 'static const unsigned char q2_expected_2[]={8,9,10,11,12,13,14,15};'
    assert lines[4].count(',') == 0x3f88 - 0x3cc8 - 1 and lines[4].startswith('static const unsigned char q2_expected_3[]={200,')
    assert lines[5:] == ['#define Q2_MCU_RANGES 3', '#define Q2_MCU_PRISTINE_RANGES 4',
                         'static const struct q2_expected q2_mcu_ranges[] = {',
                         '{0,15560,q2_expected_0},', '{27648,4,q2_expected_1},', '{32520,8,q2_expected_2},',
                         '{15560,704,q2_expected_3},', '};']
    assert pwm.expected_header(image, 4).endswith('};\n')
